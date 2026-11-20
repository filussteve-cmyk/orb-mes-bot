#!/usr/bin/env python3
"""ORB-MES paper bot (Phase 1) — first-hour range breakout on MES with the
regime switch running in SHADOW against the live Rust account.

PAPER ONLY: places ZERO orders on any account. Signals come from ES 1m
bars (shared practice identity, read-only); fills are simulated. The
switch checks (spec S1/S2 + shared levels) read the LIVE DLL account
(rust/.env identity; account id via ORBMES_LIVE_ACCOUNT_ID env, set by
the local wrapper only) read-only: if Rust is positioned when a break
fires, the paper bot skips the day exactly as the live bot will.
It writes ~/workspace/trading-bot/switch-state.json on paper fills —
Rust does not read that file until the Phase 2 patch; writing it now is
rehearsal and harmless.

Live lockout: refuses to start unless config mode == "paper" AND the
ORBMES_FORCE_PAPER=1 env var is set (the supervised wrapper sets it).
"""
import json
import os
import sys
import time as time_mod
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import yaml

import strategy_orb as S

ET = ZoneInfo("America/New_York")
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "state.json")
SWITCH_PATH = "/home/hatch/workspace/trading-bot/switch-state.json"
SHARED_ENV = "/home/hatch/workspace/trading-bot/.env"
RUST_ENV = "/home/hatch/workspace/trading-bot/rust/.env"
LIVE_ACCOUNT_ID = int(os.environ.get("ORBMES_LIVE_ACCOUNT_ID", "0"))
API = "https://api.topstepx.com"


def log(msg):
    print(f"{datetime.now(ET).strftime('%Y-%m-%d %H:%M:%S')} ET {msg}", flush=True)


def load_env_file(path):
    env = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


class Client:
    def __init__(self, user, key):
        self.user, self.key = user, key
        self.c = httpx.Client(base_url=API, timeout=30.0)
        self.tok = None

    def login(self):
        r = self.c.post("/api/Auth/loginKey",
                        json={"userName": self.user, "apiKey": self.key})
        r.raise_for_status()
        self.tok = r.json()["token"]
        return self.tok

    def post(self, path, body):
        if not self.tok:
            self.login()
        r = self.c.post(path, json=body,
                        headers={"Authorization": f"Bearer {self.tok}"})
        if r.status_code == 401:
            self.login()
            r = self.c.post(path, json=body,
                            headers={"Authorization": f"Bearer {self.tok}"})
        r.raise_for_status()
        return r.json()


def fetch_bars(client, contract_id, start_et, end_et):
    r = client.post("/api/History/retrieveBars", {
        "contractId": contract_id,
        "startTime": start_et.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endTime": end_et.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "unit": 2, "unitNumber": 1, "limit": 1000, "live": False})
    bars = []
    for b in r.get("bars", []):
        bars.append({"t": datetime.fromisoformat(b["t"]).astimezone(ET),
                     "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]})
    bars.sort(key=lambda x: x["t"])
    return bars


def live_account_snapshot(client):
    """Read-only: (any_position_open, approx_realized_today_usd).
    Realized approx = sum of closing fills' profitAndLoss minus $1.89/fill
    (ES fee/side) for the ET day window — shadow-grade, for the gate logs."""
    pos = client.post("/api/Position/searchOpen", {"accountId": LIVE_ACCOUNT_ID})
    positions = pos.get("positions", [])
    now = datetime.now(ET)
    start = now.replace(hour=5, minute=0, second=0, microsecond=0)
    tr = client.post("/api/Trade/search", {
        "accountId": LIVE_ACCOUNT_ID,
        "startTime": start.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endTime": (now + timedelta(minutes=5)).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")})
    fills = tr.get("trades", [])
    realized = sum(f["profitAndLoss"] for f in fills if f.get("profitAndLoss") is not None)
    realized -= 1.89 * len(fills)
    return len(positions) > 0, realized, positions


def load_state(today):
    try:
        st = json.load(open(STATE_PATH))
        if st.get("date") == today:
            return st
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {"date": today, "last_bar": None, "range": None, "traded": False,
            "skip_reason": None, "position": None, "day_pnl": 0.0}


def save_state(st):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1, default=str)
    os.replace(tmp, STATE_PATH)


def main():
    cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml")))
    if cfg.get("mode") != "paper":
        print("REFUSING TO START: mode is not 'paper'. Live is not approved.", flush=True)
        sys.exit(2)
    if os.environ.get("ORBMES_FORCE_PAPER") != "1":
        print("REFUSING TO START: ORBMES_FORCE_PAPER=1 not set.", flush=True)
        sys.exit(2)

    shared = load_env_file(SHARED_ENV)
    rust_env = load_env_file(RUST_ENV)
    bars_client = Client(shared["PROJECT_X_USERNAME"], shared["PROJECT_X_API_KEY"])
    live_client = Client(rust_env["PROJECT_X_USERNAME"], rust_env["PROJECT_X_API_KEY"])
    bars_client.login()
    contract_id = cfg["signal_contract_id"]
    log(f"ORB-MES starting in PAPER mode (zero orders). Signal contract {contract_id}, "
        f"instrument MES 1 contract, target {cfg['target_r']}R, "
        f"shared stop -${cfg['combined_stop']:.0f} / cap +${cfg['combined_cap']:.0f}.")
    log("Connected to TopstepX (bars identity). Live-account switch checks: SHADOW (read-only).")

    today = datetime.now(ET).strftime("%Y-%m-%d")
    st = load_state(today)
    poll = int(cfg.get("poll_seconds", 30))

    while True:
        now = datetime.now(ET)
        if now.strftime("%Y-%m-%d") != today:
            log("Date rolled over; exiting for the next launch.")
            return
        if now.time() >= time(16, 0):
            log(f"DAY SUMMARY {today}: traded={st['traded']} skip={st['skip_reason']} "
                f"paper P&L ${st['day_pnl']:.2f}. Session over.")
            save_state(st)
            return
        if now.time() < time(9, 30):
            time_mod.sleep(poll)
            continue
        try:
            start = now.replace(hour=9, minute=30, second=0, microsecond=0)
            bars = fetch_bars(bars_client, contract_id, start, now)
        except Exception as e:
            log(f"bar fetch failed: {e}")
            time_mod.sleep(poll)
            continue
        if not bars:
            time_mod.sleep(poll)
            continue
        # closed bars only: drop the still-forming final bar unless it is
        # the newest closed minute (bar t + 1min <= now)
        closed = [b for b in bars if b["t"] + timedelta(minutes=1) <= now]
        if not closed:
            time_mod.sleep(poll)
            continue
        if st["range"] is None:
            rng = S.compute_range(closed)
            if rng:
                st["range"] = rng
                log(f"First-hour range set: high {rng['high']:.2f} low {rng['low']:.2f} "
                    f"mid {rng['mid']:.2f} width {rng['width']:.2f}")
                if st["last_bar"] is None:
                    st["last_bar"] = closed[-1]["t"].isoformat()
                    log(f"Initialized at bar {closed[-1]['t'].strftime('%H:%M')} (no retro-trading).")
                save_state(st)
        if st["last_bar"] is None:
            st["last_bar"] = closed[-1]["t"].isoformat()
            save_state(st)
            time_mod.sleep(poll)
            continue
        last = datetime.fromisoformat(st["last_bar"])
        new_bars = [b for b in closed if b["t"] > last]
        for bar in new_bars:
            st["last_bar"] = bar["t"].isoformat()
            bt = bar["t"].time()
            pos = st["position"]
            if pos:
                exit_px, reason, _ = S.simulate_exit([bar], pos)
                # simulate_exit on a single bar: only exits if stop/target/time hit on THIS bar
                hit = (reason in ("stop", "target")) or bt >= S.FLAT_TIME
                if hit:
                    if bt >= S.FLAT_TIME and reason == "eod":
                        exit_px, reason = bar["o"], "time"
                    net, gross, costs = S.trade_pnl(pos, exit_px, reason)
                    st["day_pnl"] += net
                    log(f"PAPER CLOSE {pos['direction']} MES @ {exit_px:.2f} ({reason}) "
                        f"gross ${gross:.2f} costs ${costs:.2f} net ${net:.2f} | day ${st['day_pnl']:.2f}")
                    st["position"] = None
                    S.write_switch(SWITCH_PATH, today, True,
                                   "long" if pos["direction"] == 1 else "short", False)
            elif not st["traded"] and st["range"] and S.ENTRY_START <= bt <= S.ENTRY_END:
                d = S.break_direction(bar, st["range"])
                if d:
                    trade = S.make_trade(d, bar["c"], st["range"], float(cfg["target_r"]))
                    risk_usd = trade["risk_pt"] * S.POINT_VALUE
                    if not LIVE_ACCOUNT_ID:
                        st["traded"] = True
                        st["skip_reason"] = "LIVE_CHECK_UNCONFIGURED"
                        log("BREAK fired but ORBMES_LIVE_ACCOUNT_ID is not set; "
                            "cannot verify the live account — skipping (fail-closed).")
                        save_state(st)
                        continue
                    try:
                        positioned, combined_realized, positions = live_account_snapshot(live_client)
                    except Exception as e:
                        log(f"live switch check failed ({e}); treating as positioned (fail-closed), skipping.")
                        positioned, combined_realized, positions = True, 0.0, []
                    combined = combined_realized + st["day_pnl"]
                    allowed, reason = S.decide_entry(
                        positioned, combined, risk_usd,
                        combined_stop=-float(cfg["combined_stop"]),
                        combined_cap=float(cfg["combined_cap"]))
                    dname = "long" if d == 1 else "short"
                    if not allowed:
                        st["traded"] = True
                        st["skip_reason"] = reason
                        extra = ""
                        if positions:
                            p = positions[0]
                            extra = f" live position: size {p.get('size')} type {p.get('type')}"
                        log(f"BREAK {dname} @ {bar['c']:.2f} {bar['t'].strftime('%H:%M')} — {reason}.{extra} "
                            f"combined realized (approx) ${combined:.2f}, risk would be ${risk_usd:.2f}. Day done for ORB.")
                    else:
                        st["traded"] = True
                        st["position"] = trade
                        log(f"PAPER ENTRY {dname} MES @ {trade['entry']:.2f} {bar['t'].strftime('%H:%M')} "
                            f"stop {trade['stop']:.2f} target {trade['target']:.2f} risk ${risk_usd:.2f} "
                            f"(combined realized approx ${combined:.2f})")
                        S.write_switch(SWITCH_PATH, today, True, dname, True)
            if st["day_pnl"] <= -float(cfg["paper_halt"]):
                if not st["traded"]:
                    st["traded"] = True
                    st["skip_reason"] = "PAPER_HALT"
                    log(f"Paper halt hit (day ${st['day_pnl']:.2f}); no entries.")
            save_state(st)
        time_mod.sleep(poll)


def smoke():
    """Offline wiring check: synthetic trend day through strategy+switch."""
    from datetime import datetime as dt
    base = dt(2026, 11, 20, 9, 30, tzinfo=ET)
    bars = []
    px = 7000.0
    for i in range(60):
        bars.append({"t": base + timedelta(minutes=i), "o": px, "h": px + 5, "l": px - 5, "c": px})
    rng = S.compute_range(bars)
    assert rng and rng["high"] == 7005.0 and rng["low"] == 6995.0
    brk = {"t": base + timedelta(minutes=65), "o": 6994.0, "h": 6996.0, "l": 6988.0, "c": 6990.0}
    assert S.break_direction(brk, rng) == -1
    tr = S.make_trade(-1, 6990.0, rng, 1.0)
    assert tr["stop"] == 7000.0 and tr["target"] == 6980.0
    allowed, _ = S.decide_entry(False, 0.0, tr["risk_pt"] * 5.0)
    assert allowed
    blocked, reason = S.decide_entry(True, 0.0, 50.0)
    assert not blocked and reason == "SKIPPED_RUST_POSITIONED"
    print("SMOKE OK")


if __name__ == "__main__":
    if "--smoke" in sys.argv:
        smoke()
    else:
        main()
