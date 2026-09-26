#!/usr/bin/env bash
# Phase 0 check, run on the Jetson: can it reach both ducks and Reachy on the LAN?
#
#   scripts/check_lan.sh 192.168.1.41 192.168.1.42
#
# For each duck: the console page (:8080), a camera frame (/frame) and the signalling port
# (:8443). For Reachy: its daemon on localhost:8000. Nothing here moves a robot.
set -u
ok=0; bad=0
pass() { echo "  ok   $1"; ok=$((ok+1)); }
fail() { echo "  FAIL $1"; bad=$((bad+1)); }

for duck in "$@"; do
  echo "duck at $duck"
  if curl -fsS -m 5 -o /dev/null "http://$duck:8080/"; then pass "console page :8080"; else fail "console page :8080 (is mediad running? same network?)"; fi
  tmp=$(mktemp --suffix=.png)
  if curl -fsS -m 10 -o "$tmp" "http://$duck:8080/frame" && [ "$(head -c 4 "$tmp" | od -An -tx1 | tr -d ' ')" = "89504e47" ]; then
    pass "camera frame /frame ($(stat -c %s "$tmp") bytes, saved at $tmp)"
  else
    fail "camera frame /frame"; rm -f "$tmp"
  fi
  if timeout 5 bash -c "</dev/tcp/$duck/8443" 2>/dev/null; then pass "signalling port :8443"; else fail "signalling port :8443 (firewall between you and the duck?)"; fi
done

echo "reachy (Lite, on this Jetson)"
if curl -fsS -m 5 http://localhost:8000/api/daemon/status >/dev/null; then pass "daemon on :8000"; else fail "daemon on :8000 (start reachy-mini-daemon; is the USB cable in?)"; fi
if curl -fsS -m 5 http://localhost:8000/api/state/full >/dev/null; then pass "state"; else fail "state"; fi

echo "$ok ok, $bad failed"
[ "$bad" -eq 0 ]
