#!/usr/bin/env python3
"""Phase 0 replay: run the PRODUCTION strategy code over the same 44
cached sessions as the Nov 20 backtest and compare, trade for trade,
against backtest-orb60-results.json (T1 column, ES $ -> MES $ = /10).

Acceptance: same trade count, same per-day nets within $0.02, total
within $1.00 of the backtest's MES total (+$1,257.25).
Shadow switch checks are disabled here (no live account in replay) —
this validates strategy+executor only, per spec Phase 0.
"""
import json
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import strategy_orb as S

ET = ZoneInfo("America/New_York")
GD = "/home/hatch/workspace/goals/topstepx-trading-bot-setup/hidden_files"


def replay_day(bars):
    rth = [b for b in bars if (b["et"].hour, b["et"].minute) >= (9, 30) and b["et"].hour < 16]
    if len(rth) < 300:
        return None
    rng = S.compute_range(rth)
    for i in range(60, len(rth)):
        b = rth[i]
        if b["et"].time() > S.ENTRY_END:
            break
        d = S.break_direction(b, rng)
        if d:
            trade = S.make_trade(d, b["c"], rng, 1.0)
            exit_px, reason, _ = S.simulate_exit(rth[i + 1:], trade)
            net, _, _ = S.trade_pnl(trade, exit_px, reason)
            return {"time": b["et"].strftime("%H:%M"),
                    "dir": "long" if d == 1 else "short", "net": round(net, 2)}
    return {"time": None, "dir": None, "net": 0.0}


def main():
    cache = json.load(open(f"{GD}/backtest-bars-epz6.json"))
    backtest = {d["date"]: d for d in json.load(open(f"{GD}/backtest-orb60-results.json"))}
    total = 0.0; trades = 0; worst_diff = 0.0; fails = []
    for dstr in sorted(cache):
        bars = cache[dstr]
        for b in bars:
            b["et"] = datetime.fromisoformat(b["t"]).astimezone(ET)
            b["t"] = b["et"]  # strategy code expects datetime under "t"
        r = replay_day(bars)
        if r is None:
            continue
        bt = backtest.get(dstr, {})
        bt_net = round(bt["trade"]["T1"]["net"] / 10.0, 2) if bt.get("trade") else 0.0
        diff = abs(r["net"] - bt_net)
        worst_diff = max(worst_diff, diff)
        if r["dir"]:
            trades += 1
        total += r["net"]
        if diff > 0.02 or (r["dir"] is None) != (bt.get("trade") is None):
            fails.append(f"{dstr}: replay {r} vs backtest net {bt_net}")
    print(f"trades: {trades} (backtest 42)")
    print(f"total MES net: ${total:,.2f} (backtest $1,257.25)")
    print(f"worst per-day diff: ${worst_diff:.2f}")
    if fails:
        print("MISMATCHES:"); [print(" ", f) for f in fails]
    ok = trades == 42 and abs(total - 1257.25) <= 1.00 and worst_diff <= 0.02
    print("PHASE 0 REPLAY:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
