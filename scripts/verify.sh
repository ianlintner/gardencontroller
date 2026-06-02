#!/usr/bin/env bash
# End-to-end check WITHOUT the board: get an OAuth2 token via client-credentials,
# then POST a sample reading to garden-ingest. Reads creds from .env.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1090
set -a; source "$ROOT/.env"; set +a

: "${OAUTH_CLIENT_ID:?}"; : "${OAUTH_CLIENT_SECRET:?}"
TOKEN_URL="${OAUTH_TOKEN_URL:-https://roauth2.cat-herding.net/oauth/token}"
AUD="${OAUTH_AUDIENCE:-garden-ingest}"
INGEST="${INGEST_URL:-https://garden.cat-herding.net/ingest}"
DEVICE="${1:-${OAUTH_CLIENT_ID}}"

echo "1) requesting token from $TOKEN_URL ..."
JWT=$(curl -fsS -X POST "$TOKEN_URL" \
  -d grant_type=client_credentials \
  -d "client_id=${OAUTH_CLIENT_ID}" \
  -d "client_secret=${OAUTH_CLIENT_SECRET}" \
  -d "audience=${AUD}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
echo "   got JWT (${#JWT} chars). aud claim:"
echo "$JWT" | cut -d. -f2 | python3 -c 'import sys,base64,json; s=sys.stdin.read().strip(); s+="="*(-len(s)%4); print("  ", json.loads(base64.urlsafe_b64decode(s)).get("aud"))' 2>/dev/null || true

echo "2) POSTing a sample reading to $INGEST (device_id=$DEVICE) ..."
code=$(curl -s -o /tmp/ingest_resp -w "%{http_code}" -X POST "$INGEST" \
  -H "Authorization: Bearer $JWT" -H 'content-type: application/json' \
  -d "{\"device_id\":\"$DEVICE\",\"location\":\"bench\",\"readings\":{\"air_temperature_celsius\":21.3,\"air_humidity_percent\":54,\"soil\":[{\"probe\":\"bed1\",\"raw\":640,\"percent\":42}],\"rain_percent\":10,\"rain_detected\":false},\"board\":{\"rssi_dbm\":-58,\"uptime_seconds\":99}}")
echo "   HTTP $code $([ "$code" = 204 ] && echo OK || cat /tmp/ingest_resp)"
echo "3) confirm in Grafana: dashboard 'Garden Overview', device_id=$DEVICE"
