#!/bin/bash
# ORB-MES bot launcher for the systemd supervisor (orb-mes-bot.service).
# The bot reads both .env files itself (shared identity for bars, Rust
# identity for the read-only live-account switch checks) — no credential
# values are handled here. This wrapper restores the egress proxy vars,
# forces paper mode, and appends output to the dated paper log.
cd /home/hatch/workspace/orb-mes-bot || exit 1
# systemd starts services with a clean environment; the egress proxy
# variables from the interactive shell must be restored or httpx fails
# TLS with WRONG_VERSION_NUMBER. service.env holds only proxy endpoints.
[ -f /home/hatch/workspace/orb-mes-bot/service.env ] && . /home/hatch/workspace/orb-mes-bot/service.env
export ORBMES_FORCE_PAPER=1
export ORBMES_LIVE_ACCOUNT_ID=0   # set to your live account id locally; 0 = switch checks fail closed
unset no_proxy NO_PROXY
LOG=/home/hatch/workspace/orb-mes-bot/paper-$(TZ=America/New_York date +%F).log
exec /home/hatch/workspace/s4-bot/.venv/bin/python bot.py >> "$LOG" 2>&1
