#!/usr/bin/env bash
# Live smoke test — confirm the agent works end-to-end against the real Anthropic API.
#
# Runs the agent (native tool-use) on the first few emails of a bundle, prints the per-email
# plan + tool-call trace and the measured cost. Cheap (a few cents) and fast (~30s).
#
# Usage:
#   export ANTHROPIC_API_KEY=sk-ant-...
#   scripts/smoke_live.sh                       # 3 emails of data/sample_bundle
#   scripts/smoke_live.sh <bundle> <n_emails>   # custom bundle / count
set -euo pipefail

BUNDLE="${1:-data/sample_bundle}"
N="${2:-3}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# --- preflight ---------------------------------------------------------------
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "✗ ANTHROPIC_API_KEY is not set."
  echo "  Run:  export ANTHROPIC_API_KEY=sk-ant-...   then re-run this script."
  exit 1
fi

# Trust the environment's outbound-proxy CA if present (usually already configured).
if [[ -f /root/.ccr/ca-bundle.crt ]]; then
  export SSL_CERT_FILE="${SSL_CERT_FILE:-/root/.ccr/ca-bundle.crt}"
  export REQUESTS_CA_BUNDLE="${REQUESTS_CA_BUNDLE:-/root/.ccr/ca-bundle.crt}"
fi

if [[ ! -e "$BUNDLE" ]]; then
  echo "✗ Bundle not found: $BUNDLE"
  exit 1
fi

echo "▶ Live smoke: first $N emails of $BUNDLE  (real Anthropic native tool-use)"
echo "  key: ${ANTHROPIC_API_KEY:0:10}…   ceiling: \$5"
echo

# --- run (live: no --mock, so it uses the real provider) ---------------------
if python3 -m pbc_agent.cli run \
      --bundle "$BUNDLE" --max-emails "$N" --trace \
      --out out/smoke_live.json; then
  echo
  echo "✓ Live path works. Full report: out/smoke_live.json"
  echo "  Next: run the whole inbox live →  python3 -m pbc_agent.cli run --bundle \"$BUNDLE\""
  echo "        or open the UI          →  python3 -m pbc_agent.cli web --bundle \"$BUNDLE\""
else
  code=$?
  echo
  echo "✗ Live run failed (exit $code). Common causes:"
  echo "    • bad/rotated API key  • no credits/billing  • network/proxy egress blocked"
  echo "  The offline path still works:  python3 -m pbc_agent.cli run --bundle \"$BUNDLE\" --mock"
  exit $code
fi
