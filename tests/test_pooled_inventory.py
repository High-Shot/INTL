"""Tests for the EU pooled-inventory detection in normalize.build_snapshot().

The pooling rule (normalize.py):
    An ASIN in a shared FBA pool (the CC_EU pool: DE/FR/IT/ES/NL/...) is
    treated as pooled ONLY when every member market that reports it shows the
    SAME available count AND more than one market reports it. When available
    counts differ, each market stands alone (pool_* fields stay None).

build_snapshot() reads its inputs from `<normalize.ROOT>/data/raw/<week>/`, so
these tests point ROOT at a temporary directory, drop a tiny si_inventory.csv
in it, and assert on the returned snapshot. No repo files are touched and
nothing is written to disk (build_snapshot only reads; main() does the write).
"""
import os

import normalize


# A future ISO week so the value can never collide with real snapshot data.
WEEK = "2099-W01"
GENERATED = "2099-01-01T00:00:00Z"

POOLED_ASIN = "B0POOLED001"
SOLO_ASIN = "B0SOLO00001"


def write_si(week_dir, rows):
    header = "market,asin,name,fba,transfer,inbound,units30,vel,doc"
    lines = [header] + rows
    with open(os.path.join(week_dir, "si_inventory.csv"), "w", newline="") as f:
        f.write("\n".join(lines) + "\n")


def build(tmp_path, monkeypatch, rows):
    week_dir = tmp_path / "data" / "raw" / WEEK
    week_dir.mkdir(parents=True)
    write_si(str(week_dir), rows)
    monkeypatch.setattr(normalize, "ROOT", str(tmp_path))
    return normalize.build_snapshot(WEEK, GENERATED)


def find_row(snap, code, asin):
    for row in snap["accounts"][code]["inventory"]:
        if row["asin"] == asin:
            return row
    raise AssertionError(f"no row for {asin} in account {code}")


def test_equal_available_across_eu_markets_is_pooled(tmp_path, monkeypatch):
    # DE and FR both report available=100 for the same ASIN -> pooled.
    snap = build(
        tmp_path,
        monkeypatch,
        [
            f"DE,{POOLED_ASIN},Pooled Item,100,0,0,30,1.0,100",
            f"FR,{POOLED_ASIN},Pooled Item,100,0,0,30,1.0,100",
        ],
    )
    de = find_row(snap, "CC_DE", POOLED_ASIN)
    fr = find_row(snap, "CC_FR", POOLED_ASIN)

    # Pooled markets recorded on both member rows.
    assert de["pool"] == "CC_EU"
    assert de["pool_markets"] == ["DE", "FR"]
    assert fr["pool_markets"] == ["DE", "FR"]
    # Pooled velocity is the sum across member markets (1.0 + 1.0).
    assert de["pool_vel"] == 2.0
    # pool_doc = available / pooled_vel = 100 / 2.0 = 50.0
    assert de["pool_doc"] == 50.0


def test_differing_available_across_eu_markets_is_not_pooled(tmp_path, monkeypatch):
    # DE=50, FR=80 -> available counts differ -> each market stands alone.
    snap = build(
        tmp_path,
        monkeypatch,
        [
            f"DE,{SOLO_ASIN},Solo Item,50,0,0,30,1.0,50",
            f"FR,{SOLO_ASIN},Solo Item,80,0,0,30,1.0,80",
        ],
    )
    de = find_row(snap, "CC_DE", SOLO_ASIN)
    fr = find_row(snap, "CC_FR", SOLO_ASIN)

    assert de["pool_markets"] is None
    assert de["pool_vel"] is None
    assert de["pool_doc"] is None
    assert fr["pool_markets"] is None


def test_pooled_and_solo_asins_coexist(tmp_path, monkeypatch):
    # Same snapshot: one ASIN pooled (equal), one not (unequal).
    snap = build(
        tmp_path,
        monkeypatch,
        [
            f"DE,{POOLED_ASIN},Pooled Item,100,0,0,30,1.0,100",
            f"FR,{POOLED_ASIN},Pooled Item,100,0,0,30,1.0,100",
            f"DE,{SOLO_ASIN},Solo Item,50,0,0,30,1.0,50",
            f"FR,{SOLO_ASIN},Solo Item,80,0,0,30,1.0,80",
        ],
    )
    assert find_row(snap, "CC_DE", POOLED_ASIN)["pool_markets"] == ["DE", "FR"]
    assert find_row(snap, "CC_DE", SOLO_ASIN)["pool_markets"] is None


def test_single_market_in_pool_is_not_pooled(tmp_path, monkeypatch):
    # Only DE reports the ASIN: one market cannot pool even inside CC_EU.
    snap = build(
        tmp_path,
        monkeypatch,
        [f"DE,{POOLED_ASIN},Pooled Item,100,0,0,30,1.0,100"],
    )
    de = find_row(snap, "CC_DE", POOLED_ASIN)
    assert de["pool_markets"] is None
    assert de["pool_vel"] is None
