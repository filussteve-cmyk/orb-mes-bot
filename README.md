# ORB-MES trend sleeve (paper, Phase 1)

First-hour range breakout on MES with a regime switch against the live
Rust bot, per the spec:
`~/workspace/goals/topstepx-trading-bot-setup/files/orb-mes-spec-2026-11-20.md`
(Steve's decisions Nov 20, 2026: 1R target; shared -$600/+$1,400 levels;
conflict rule S2 — ORB skips, never touches Rust; build Phase 0/1.)

## Rules (as backtested Nov 20, 2026)
- Range = high/low of 09:30–10:29 ET ES 1m bars. Entry = first bar at or
  after 10:30 that CLOSES beyond the range; last entry 15:30; one trade
  per day, no flip. Stop = range midpoint. Target = 1R. Flat 15:50.
- Paper P&L at MES $5/pt with the backtest cost model (fee $0.38 RT +
  1 tick on entry/stop/time exits). Signals use ES bars; MES tracks ES
  point-for-point. ZERO orders are placed on any account.
- Backtest (44 sessions Sep 22–Nov 20 2026): 42 trades, 60% win,
  ≈ +$1,257 MES at 1R. Phase 0 replay of this production code
  reproduced it exactly (42 trades, $1,257.24).

## The switch (shadow in Phase 1)
At a break, the bot reads the LIVE DLL account read-only (account id
supplied locally via the ORBMES_LIVE_ACCOUNT_ID env var, never in code):
- Rust positioned → skip the day (`SKIPPED_RUST_POSITIONED`), log it.
- Combined realized (live approx + this bot's paper P&L) vs the shared
  -$600 stop / +$1,400 cap, and the dynamic floor (combined − risk ≥
  -$900) — all enforced in paper, logged with inputs.
- On a paper fill it writes `~/workspace/trading-bot/switch-state.json`
  (date-keyed). Rust does NOT read it until the Phase 2 patch — writing
  it now is rehearsal. `strategy_orb.rust_blocked()` is the reader the
  Rust patch will mirror: missing/stale = unblocked, corrupt = blocked.

## Safety
- Refuses to start unless config `mode: "paper"` AND env
  `ORBMES_FORCE_PAPER=1` (wrapper sets it). No live code path exists.
- Live-account access is read-only (Position/searchOpen, Trade/search).

## Run
- systemd: `orb-mes-bot.service` + `run_supervised.sh` (master copies
  here), weekday auto-launch cron `orb-mes-auto-launch-weekday` 09:20 ET.
- Tests: `.venv`-python `-m pytest test_strategy_orb.py -q` (8 tests).
- Replay: `python replay.py` (Phase 0 acceptance vs the backtest).
- Smoke: `python bot.py --smoke`.
