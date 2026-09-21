#!/bin/bash
# The headless contract, as promised to agents in llms.txt and README.md.
#
# Every line here is a claim those files make: which commands work without a
# TTY, which emit JSON, and which exit codes mean what. Agents build on that
# contract, and they cannot read a changelog -- so it is asserted.
#
#   bash reality-router/tests/test_headless_contract.sh
#
# Uses a throwaway REALITY_ROUTER_HOME; never touches a real install. The last
# section is skipped unless a router is already serving on $RR_PORT.

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CLI="python3 $ROOT/start_router.py"
H="$(mktemp -d)"
RR_PORT="${RR_PORT:-8000}"
PASS=0; FAIL=0

isjson() {
  python3 - "$1" <<'PY'
import sys, json
t = open(sys.argv[1]).read().strip()
if not t:
    sys.exit(1)
try:                      # a single JSON document
    json.loads(t); sys.exit(0)
except Exception:
    pass
try:                      # or NDJSON, as the auth device flow emits
    [json.loads(l) for l in t.splitlines() if l.strip()]; sys.exit(0)
except Exception:
    sys.exit(1)
PY
}

run() { # name  expected_exit|*  expect_json(y/n)  command...
  local name="$1" exp="$2" js="$3"; shift 3
  "$@" </dev/null >"$H/out" 2>"$H/err"; local rc=$?
  local ok=1 why=""
  [ "$exp" != "*" ] && [ "$rc" != "$exp" ] && { ok=0; why="exit=$rc want=$exp"; }
  [ "$js" = "y" ] && { isjson "$H/out" || { ok=0; why="$why; stdout not JSON"; }; }
  if [ $ok = 1 ]; then
    PASS=$((PASS+1)); printf "PASS  %-40s exit=%s\n" "$name" "$rc"
  else
    FAIL=$((FAIL+1)); printf "FAIL  %-40s %s\n" "$name" "$why"
    printf "      stdout: %s\n" "$(head -c 120 "$H/out" | tr '\n' ' ')"
    [ -s "$H/err" ] && printf "      stderr: %s\n" "$(head -c 120 "$H/err" | tr '\n' ' ')"
  fi
}

echo "== nothing configured"
export REALITY_ROUTER_HOME="$H"
run "status --json (not running)"         0  y $CLI status --json
run "doctor --json (no config)"           10 y $CLI doctor --json
run "setup --non-interactive (no keys)"   3  n $CLI setup --non-interactive
run "auth --non-interactive --json"       3  y $CLI auth --non-interactive --json
run "stop --json (not running)"           0  y $CLI stop --json

echo "== provider key present, router not running"
echo "OPENAI_API_KEY=sk-not-a-real-key" > "$H/.env"
run "doctor --json (no SSO token)"        11 y $CLI doctor --json
run "--set KEY=VALUE --json"              0  y $CLI --set OPENAI_API_KEY=sk-also-not-real --json
run "models --json"                       0  y $CLI models --json
run "models --disable-model <id> --json"  0  y $CLI models --disable-model gpt-5 --json
run "models --enable-model <id> --json"   0  y $CLI models --enable-model gpt-5 --json

if curl -sf "http://127.0.0.1:$RR_PORT/health" >/dev/null 2>&1; then
  echo "== a router is serving on $RR_PORT (however it was started)"
  unset REALITY_ROUTER_HOME
  run "status --json says running"        0  y $CLI status --json
  run "doctor --json says healthy"        0  y $CLI doctor --json
else
  echo "== skipped: no router serving on $RR_PORT (set RR_PORT to change)"
fi

rm -rf "$H"
echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" = 0 ]
