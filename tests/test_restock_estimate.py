"""Tests for the restock-quantity estimate in normalize.build_snapshot().

Fallback formula (used when Amazon's own recommendation is not loaded), from
normalize.py:

    horizon = LEAD_TIME_DAYS[pool] + REVIEW_COVER_DAYS
    est     = max(0, round(vel * horizon) - available - inbound)

computed only for items whose severity is CRITICAL/URGENT/WATCH and whose
(possibly pooled) velocity is > 0. Constants in the current code:
    REVIEW_COVER_DAYS = 30
    lead time: NA pools 14 days, EU/UK/AU/SA pools 45 days

Inputs are read from `<normalize.ROOT>/data/raw/<week>/`, so ROOT is pointed at
a temp dir with a tiny si_inventory.csv. build_snapshot() only reads.
"""
import os

import normalize

WEEK = "2099-W02"
GENERATED = "2099-01-08T00:00:00Z"


def build(tmp_path, monkeypatch, rows):
    week_dir = tmp_path / "data" / "raw" / WEEK
    week_dir.mkdir(parents=True)
    header = "market,asin,name,fba,transfer,inbound,units30,vel,doc"
    with open(os.path.join(str(week_dir), "si_inventory.csv"), "w", newline="") as f:
        f.write("\n".join([header] + rows) + "\n")
    monkeypatch.setattr(normalize, "ROOT", str(tmp_path))
    return normalize.build_snapshot(WEEK, GENERATED)


def find_row(snap, code, asin):
    for row in snap["accounts"][code]["inventory"]:
        if row["asin"] == asin:
            return row
    raise AssertionError(f"no row for {asin} in account {code}")


def test_solo_eu_urgent_estimate(tmp_path, monkeypatch):
    # CC_DE pool = CC_EU (lead 45). horizon = 45 + 30 = 75.
    # URGENT: doc 10 < 14 with nothing inbound. est = round(1.0*75) - 10 - 0 = 65.
    snap = build(
        tmp_path, monkeypatch, ["DE,B0EST00001,Item,10,0,0,30,1.0,10"]
    )
    row = find_row(snap, "CC_DE", "B0EST00001")
    assert row["severity"] == "URGENT"
    assert row["restock_est"] == 65


def test_na_lead_time_is_shorter(tmp_path, monkeypatch):
    # CL_US pool = CL_US (lead 14). horizon = 14 + 30 = 44.
    # URGENT: doc 10 < 14. est = round(1.0*44) - 5 - 0 = 39.
    snap = build(
        tmp_path, monkeypatch, ["CL_US,B0EST00002,Item,5,0,0,30,1.0,10"]
    )
    row = find_row(snap, "CL_US", "B0EST00002")
    assert row["severity"] == "URGENT"
    assert row["restock_est"] == 39


def test_inbound_is_subtracted_from_estimate(tmp_path, monkeypatch):
    # DE, doc 10 with inbound 20 -> WATCH (still estimated). horizon = 75.
    # est = round(1.0*75) - 10 - 20 = 45.
    snap = build(
        tmp_path, monkeypatch, ["DE,B0EST00003,Item,10,0,20,30,1.0,10"]
    )
    row = find_row(snap, "CC_DE", "B0EST00003")
    assert row["severity"] == "WATCH"
    assert row["restock_est"] == 45


def test_pooled_estimate_uses_summed_velocity(tmp_path, monkeypatch):
    # DE and FR pooled (equal available 10), each vel 1.0 -> pool_vel 2.0.
    # pool_doc = 10 / 2.0 = 5 < 14 -> URGENT. horizon = 75.
    # est = round(2.0*75) - 10 - 0 = 140.
    snap = build(
        tmp_path,
        monkeypatch,
        [
            "DE,B0EST00004,Item,10,0,0,30,1.0,100",
            "FR,B0EST00004,Item,10,0,0,30,1.0,100",
        ],
    )
    row = find_row(snap, "CC_DE", "B0EST00004")
    assert row["pool_vel"] == 2.0
    assert row["severity"] == "URGENT"
    assert row["restock_est"] == 140


def test_healthy_item_has_no_estimate(tmp_path, monkeypatch):
    # OK severity -> restock_est stays None (est only computed for C/U/WATCH).
    snap = build(
        tmp_path, monkeypatch, ["DE,B0EST00005,Item,1000,0,0,30,1.0,1000"]
    )
    row = find_row(snap, "CC_DE", "B0EST00005")
    assert row["severity"] == "OK"
    assert row["restock_est"] is None
