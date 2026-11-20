"""ORB-MES trend sleeve — pure strategy + regime-switch logic.

Implements the spec in
~/workspace/goals/topstepx-trading-bot-setup/files/orb-mes-spec-2026-11-20.md
(Steve's decisions Nov 20, 2026: 1R target, shared -$600/+$1,400 levels,
conflict rule S2 = ORB skips, build Phase 0/1).

No I/O except the switch-file helpers at the bottom. Bars are dicts:
{"t": datetime (ET), "o","h","l","c": float}.
"""
import json
import os
from datetime import datetime, time

RANGE_BARS = 60                 # first hour: 09:30-10:29 ET
ENTRY_START = time(10, 30)      # first break bar may be the 10:30 bar
ENTRY_END = time(15, 30)        # last entry
FLAT_TIME = time(15, 50)        # time exit
POINT_VALUE = 5.0               # MES $/pt
FEE_RT = 0.38                   # modeled round-turn fee (backtest parity;
                                # verify the real TopstepX MES fee pre-live)
TICK_USD = 1.25                 # MES tick value = modeled slippage unit


def compute_range(bars):
    """bars: the day's RTH 1m bars in order. Returns dict or None."""
    if len(bars) < RANGE_BARS:
        return None
    first = bars[:RANGE_BARS]
    high = max(b["h"] for b in first)
    low = min(b["l"] for b in first)
    return {"high": high, "low": low, "mid": (high + low) / 2.0,
            "width": high - low}


def break_direction(bar, rng):
    """+1 long / -1 short if this bar CLOSES beyond the range, else 0."""
    if bar["c"] > rng["high"]:
        return 1
    if bar["c"] < rng["low"]:
        return -1
    return 0


def make_trade(direction, entry_px, rng, target_r=1.0):
    stop = rng["mid"]
    risk = abs(entry_px - stop)
    return {"direction": direction, "entry": entry_px, "stop": stop,
            "risk_pt": risk, "target": entry_px + direction * target_r * risk}


def simulate_exit(bars, trade):
    """Walk bars AFTER the entry bar. Pessimistic intrabar order (stop
    before target). Time exit at the 15:50 bar's open.
    Returns (exit_px, reason, exit_index)."""
    sgn = trade["direction"]
    for i, b in enumerate(bars):
        if b["t"].time() >= FLAT_TIME:
            return b["o"], "time", i
        hit_stop = b["l"] <= trade["stop"] if sgn == 1 else b["h"] >= trade["stop"]
        hit_tgt = b["h"] >= trade["target"] if sgn == 1 else b["l"] <= trade["target"]
        if hit_stop and hit_tgt:
            return trade["stop"], "stop", i
        if hit_tgt:
            return trade["target"], "target", i
        if hit_stop:
            return trade["stop"], "stop", i
    last = bars[-1]
    return last["c"], "eod", len(bars) - 1


def trade_pnl(trade, exit_px, reason):
    gross = (exit_px - trade["entry"]) * trade["direction"] * POINT_VALUE
    costs = FEE_RT + TICK_USD + (0.0 if reason == "target" else TICK_USD)
    return gross - costs, gross, costs


# ---------------- regime switch (spec S1-S7) ----------------

def decide_entry(rust_position_open, combined_realized, risk_usd,
                 combined_stop=-600.0, combined_cap=1400.0, dll_floor=-900.0):
    """Pure form of the entry gate. Returns (allowed, reason).
    S2: Rust positioned -> ORB skips the day.
    Shared levels (Steve, Nov 20 2026): combined stop -$600, cap +$1,400,
    dynamic check: combined_realized - this trade's risk must stay >= -$900."""
    if rust_position_open:
        return False, "SKIPPED_RUST_POSITIONED"
    if combined_realized <= combined_stop:
        return False, "COMBINED_DAILY_STOP"
    if combined_realized >= combined_cap:
        return False, "COMBINED_DAILY_CAP"
    if combined_realized - risk_usd < dll_floor:
        return False, "DYNAMIC_DLL_FLOOR"
    return True, "OK"


def read_switch(path):
    """Returns (state_dict_or_None, corrupt_bool)."""
    try:
        with open(path) as f:
            return json.load(f), False
    except FileNotFoundError:
        return None, False
    except (json.JSONDecodeError, OSError):
        return None, True


def rust_blocked(path, today_str):
    """S3/S4/S6: Rust must not enter when ORB traded or is open today.
    Missing/stale file = not blocked. Corrupt file = BLOCKED (fail-closed)."""
    state, corrupt = read_switch(path)
    if corrupt:
        return True
    if not state or state.get("date") != today_str:
        return False
    return bool(state.get("orb_traded") or state.get("orb_open"))


def write_switch(path, today_str, orb_traded, orb_direction, orb_open):
    state = {"date": today_str, "orb_traded": orb_traded,
             "orb_direction": orb_direction, "orb_open": orb_open,
             "updated_at": datetime.now().isoformat(timespec="seconds")}
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, path)
    return state
