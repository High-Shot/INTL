"""Unit tests for normalize.classify() severity boundaries.

classify(item, pool_vel, pool_doc) is a pure function: given one inventory
item dict plus optional pooled velocity/days-of-cover overrides, it returns
(severity, reasons[]). These tests lock in the severity decision boundaries
exactly as the current code computes them. They must pass against the
committed normalize.py without modifying it.

Project rules under test (from normalize.py):
  URGENT_DOC = 14, WATCH_DOC = 28  (days of cover)
  UNFULFILLABLE_WATCH = 10
"""
import normalize


def make_item(**over):
    """Minimal item with all keys classify() reads. Override per test."""
    item = {
        "available": 100,
        "inbound": 0,
        "inbound_reported": True,
        "vel": 1.0,
        "doc": 60.0,
        "units30": 30,
        "unfulfillable": 0,
        "ad30": 0,
    }
    item.update(over)
    return item


# --- Out-of-stock branches -------------------------------------------------

def test_oos_nothing_inbound_selling_is_critical():
    sev, reasons = normalize.classify(
        make_item(available=0, inbound=0, doc=None, units30=10, vel=1.0), None, None
    )
    assert sev == "CRITICAL"
    assert "Out of stock, nothing inbound" in reasons


def test_oos_nothing_inbound_dormant_is_info():
    sev, reasons = normalize.classify(
        make_item(available=0, inbound=0, doc=None, units30=0, vel=0), None, None
    )
    assert sev == "INFO"
    assert reasons == ["Out of stock, no sales in 30d (dormant)"]


def test_oos_with_inbound_selling_is_urgent():
    sev, reasons = normalize.classify(
        make_item(available=0, inbound=50, doc=None, units30=10, vel=1.0), None, None
    )
    assert sev == "URGENT"
    assert "Out of stock, 50 inbound" in reasons


def test_oos_with_inbound_dormant_is_info():
    sev, reasons = normalize.classify(
        make_item(available=0, inbound=50, doc=None, units30=0, vel=0), None, None
    )
    assert sev == "INFO"
    assert "Out of stock, 50 inbound, no sales in 30d" in reasons


# --- Days-of-cover boundaries ---------------------------------------------

def test_doc_just_under_urgent_no_inbound_is_urgent():
    sev, _ = normalize.classify(
        make_item(available=10, inbound=0, doc=13.9), None, None
    )
    assert sev == "URGENT"


def test_doc_exactly_urgent_threshold_is_watch():
    # doc == URGENT_DOC (14) is NOT < 14, so it falls to the < WATCH_DOC branch.
    sev, _ = normalize.classify(
        make_item(available=10, inbound=0, doc=14.0), None, None
    )
    assert sev == "WATCH"


def test_doc_under_urgent_with_inbound_is_watch():
    sev, reasons = normalize.classify(
        make_item(available=10, inbound=5, doc=13.9), None, None
    )
    assert sev == "WATCH"
    assert "13.9 days of cover, 5 inbound" in reasons


def test_doc_under_watch_no_inbound_is_watch():
    sev, _ = normalize.classify(
        make_item(available=10, inbound=0, doc=27.9), None, None
    )
    assert sev == "WATCH"


def test_doc_exactly_watch_threshold_is_ok():
    # doc == WATCH_DOC (28) is NOT < 28, so no stock branch fires -> OK.
    sev, reasons = normalize.classify(
        make_item(available=10, inbound=0, doc=28.0), None, None
    )
    assert sev == "OK"
    assert reasons == []


def test_mid_doc_with_inbound_falls_through_to_ok():
    # doc between URGENT_DOC and WATCH_DOC WITH inbound matches no branch -> OK.
    sev, _ = normalize.classify(
        make_item(available=10, inbound=5, doc=20.0), None, None
    )
    assert sev == "OK"


# --- Modifiers on top of the base severity --------------------------------

def test_unfulfillable_bumps_ok_to_watch():
    sev, reasons = normalize.classify(
        make_item(available=100, inbound=0, doc=60.0, unfulfillable=12), None, None
    )
    assert sev == "WATCH"
    assert any("unfulfillable" in r for r in reasons)


def test_unfulfillable_below_threshold_stays_ok():
    sev, reasons = normalize.classify(
        make_item(available=100, inbound=0, doc=60.0, unfulfillable=9), None, None
    )
    assert sev == "OK"
    assert not any("unfulfillable" in r for r in reasons)


def test_ads_still_running_reason_when_critical():
    sev, reasons = normalize.classify(
        make_item(available=0, inbound=0, doc=None, units30=10, vel=1.0, ad30=5),
        None,
        None,
    )
    assert sev == "CRITICAL"
    assert "Ads still running" in reasons


def test_inbound_unknown_reason_when_not_reported():
    sev, reasons = normalize.classify(
        make_item(
            available=0, inbound=None, inbound_reported=False, doc=None,
            units30=10, vel=1.0,
        ),
        None,
        None,
    )
    assert sev == "CRITICAL"
    assert "Inbound unknown (not reported), treated as 0" in reasons


# --- Pooled overrides take precedence over per-item vel/doc ----------------

def test_pool_doc_override_drives_severity():
    # Item's own doc is healthy (100) but the pooled days-of-cover is < URGENT.
    sev, reasons = normalize.classify(
        make_item(available=10, inbound=0, doc=100.0, vel=0.1, units30=0),
        pool_vel=2.0,
        pool_doc=13.0,
    )
    assert sev == "URGENT"
    assert "13.0 days of cover, nothing inbound" in reasons


def test_pool_vel_marks_item_selling():
    # units30 == 0 and item vel ~0, but a positive pooled velocity means the
    # OOS item counts as selling -> CRITICAL rather than INFO.
    sev, _ = normalize.classify(
        make_item(available=0, inbound=0, doc=None, units30=0, vel=0),
        pool_vel=2.0,
        pool_doc=None,
    )
    assert sev == "CRITICAL"
