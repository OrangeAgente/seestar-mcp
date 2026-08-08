#!/usr/bin/env bash
#
# Dawn park watchdog — the layer that survives everything else.
#
# WHY THIS EXISTS
#   The agent driving a session is turn-based: it only runs while something
#   invokes it. If the Claude Code session ends, the client closes, the laptop
#   sleeps, or the context runs out, nothing is left to park the scope — and the
#   mount sits open until someone notices.
#
#   This script depends on NONE of that. No MCP, no agent, no Python. It talks
#   straight to seestar_alp over HTTP, waits for a deadline, stops the view and
#   parks. `run-session` Phase 0 and `autonomous-night` Phase A0 both REQUIRE it
#   to be armed before any motion.
#
#   Proven in the field: armed 03:24Z on 2026-08-04, fired at 07:40Z, parked and
#   confirmed the arm folded at 07:41:19Z, while the agent had been dormant for
#   four hours.
#
# USAGE
#   deploy/dawn_park_watchdog.sh <park-time-utc> [log-file] [alpaca-base-url]
#
#   # park at 07:40 UTC today
#   deploy/dawn_park_watchdog.sh "2026-08-04 07:40:00" /tmp/watchdog.log
#
#   Run it DETACHED so it outlives your shell:
#   nohup deploy/dawn_park_watchdog.sh "2026-08-04 07:40:00" /tmp/wd.log >/dev/null 2>&1 &
#
# EXIT CODES
#   0  parked, or the mount was already folded
#   1  park not confirmed within the timeout (check the log and the scope)
#   2  bad arguments
#
set -u

PARK_AT="${1:-}"
LOG="${2:-/dev/stdout}"
BASE="${3:-${SEESTAR_ALPACA_BASE_URL:-http://127.0.0.1:5555}}"
DEVICE="${SEESTAR_ALPACA_DEVICE_NUM:-1}"
ACTION="${BASE}/api/v1/telescope/${DEVICE}/action"

if [ -z "$PARK_AT" ]; then
  echo "usage: $0 <park-time-utc> [log-file] [alpaca-base-url]" >&2
  exit 2
fi

PARK_EPOCH=$(date -u -d "$PARK_AT" +%s 2>/dev/null)
if [ -z "$PARK_EPOCH" ]; then
  echo "could not parse park time: $PARK_AT" >&2
  exit 2
fi

say() { echo "[$(date -u +%H:%M:%SZ)] $*" >> "$LOG"; }

# One native JSON-RPC call through seestar_alp's action tunnel.
call() {
  curl -s -m 25 -X PUT "$ACTION" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "Action=method_sync" \
    --data-urlencode "Parameters=$1" \
    --data-urlencode "ClientID=77" \
    --data-urlencode "ClientTransactionID=$RANDOM" 2>/dev/null
}

# `mount.close: true` means the arm is FOLDED. This is the authoritative park
# signal — Alpaca's own /atpark disagrees with the device on firmware 7.75 and
# 8.46, so do not use it here.
is_folded() { call '{"method":"get_device_state"}' | grep -o '"close": *true' | head -1; }

say "watchdog armed; park at $(date -u -d "@$PARK_EPOCH" +%Y-%m-%dT%H:%M:%SZ) (bridge $BASE, device $DEVICE)"

while [ "$(date +%s)" -lt "$PARK_EPOCH" ]; do
  sleep 120
done

say "park deadline reached"

if [ -n "$(is_folded)" ]; then
  say "mount already folded — nothing to do"
  exit 0
fi

say "stopping view"
call '{"method":"iscope_stop_view"}' >/dev/null
sleep 5

say "parking"
call '{"method":"scope_park"}' >/dev/null

for _ in $(seq 1 20); do
  sleep 15
  if [ -n "$(is_folded)" ]; then
    say "PARKED — arm folded"
    exit 0
  fi
done

say "WARNING: park not confirmed after 5 minutes — CHECK THE SCOPE"
exit 1
