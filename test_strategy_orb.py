"""Unit tests for ORB-MES strategy + switch logic."""
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strategy_orb as S

ET = ZoneInfo("America/New_York")
BASE = datetime(2026, 11, 20, 9, 30, tzinfo=ET)


def bar(i, o, h, l, c):
    return {"t": BASE + timedelta(minutes=i), "o": o, "h": h, "l": l, "c": c}


def flat_range_bars():
    return [bar(i, 100.0, 105.0, 95.0, 100.0) for i in range(60)]


def test_range_math():
    rng = S.compute_range(flat_range_bars())
    assert rng == {"high": 105.0, "low": 95.0, "mid": 100.0, "width": 10.0}
    assert S.compute_range(flat_range_bars()[:59]) is None


def test_break_requires_close_beyond():
    rng = S.compute_range(flat_range_bars())
    assert S.break_direction(bar(60, 100, 106, 99, 104), rng) == 0   # wick only
    assert S.break_direction(bar(60, 100, 106, 99, 105.25), rng) == 1
    assert S.break_direction(bar(60, 100, 101, 94, 94.75), rng) == -1
    assert S.break_direction(bar(60, 100, 101, 99, 100), rng) == 0


def test_trade_math_long_and_short():
    rng = S.compute_range(flat_range_bars())
    t = S.make_trade(1, 106.0, rng, 1.0)
    assert t["stop"] == 100.0 and t["risk_pt"] == 6.0 and t["target"] == 112.0
    t = S.make_trade(-1, 94.0, rng, 1.0)
    assert t["stop"] == 100.0 and t["risk_pt"] == 6.0 and t["target"] == 88.0
    t = S.make_trade(1, 106.0, rng, 2.0)
    assert t["target"] == 118.0


def test_exit_pessimistic_stop_first():
    rng = S.compute_range(flat_range_bars())
    t = S.make_trade(1, 106.0, rng, 1.0)
    both = bar(61, 106, 113, 99, 107)          # spans stop AND target
    px, why, _ = S.simulate_exit([both], t)
    assert (px, why) == (100.0, "stop")


def test_exit_target_and_time():
    rng = S.compute_range(flat_range_bars())
    t = S.make_trade(-1, 94.0, rng, 1.0)
    px, why, _ = S.simulate_exit([bar(61, 94, 95, 87, 90)], t)
    assert (px, why) == (88.0, "target")
    late = bar(380, 93.0, 94.0, 92.0, 93.0)    # 15:50 bar
    assert late["t"].time() >= S.FLAT_TIME
    px, why, _ = S.simulate_exit([late], t)
    assert (px, why) == (93.0, "time")


def test_pnl_costs():
    rng = S.compute_range(flat_range_bars())
    t = S.make_trade(1, 106.0, rng, 1.0)
    net, gross, costs = S.trade_pnl(t, 112.0, "target")
    assert gross == 30.0 and abs(costs - (0.38 + 1.25)) < 1e-9
    net, gross, costs = S.trade_pnl(t, 100.0, "stop")
    assert gross == -30.0 and abs(costs - (0.38 + 2.50)) < 1e-9


def test_decide_entry_switch_rules():
    assert S.decide_entry(False, 0.0, 100.0) == (True, "OK")
    ok, r = S.decide_entry(True, 0.0, 100.0)
    assert not ok and r == "SKIPPED_RUST_POSITIONED"          # S2
    ok, r = S.decide_entry(False, -600.0, 100.0)
    assert not ok and r == "COMBINED_DAILY_STOP"
    ok, r = S.decide_entry(False, 1400.0, 100.0)
    assert not ok and r == "COMBINED_DAILY_CAP"
    ok, r = S.decide_entry(False, -350.0, 612.50)
    assert not ok and r == "DYNAMIC_DLL_FLOOR"                # -962.50 < -900
    ok, r = S.decide_entry(False, -350.0, 500.0)
    assert ok                                                 # -850 >= -900


def test_switch_file_rules(tmp_path):
    p = str(tmp_path / "switch-state.json")
    assert S.rust_blocked(p, "2026-11-20") is False           # missing
    S.write_switch(p, "2026-11-20", True, "short", False)
    assert S.rust_blocked(p, "2026-11-20") is True            # traded today
    assert S.rust_blocked(p, "2026-11-21") is False           # stale date
    S.write_switch(p, "2026-11-20", False, None, False)
    assert S.rust_blocked(p, "2026-11-20") is False           # nothing happened
    with open(p, "w") as f:
        f.write("{corrupt")
    assert S.rust_blocked(p, "2026-11-20") is True            # fail-closed
