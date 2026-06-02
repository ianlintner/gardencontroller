#!/usr/bin/env bash
# Generate secrets, compile, and upload the firmware to a connected UNO R4 WiFi.
# Auto-detects the serial port (override with: ./scripts/flash.sh <port>).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKETCH="$ROOT/firmware/garden-node"
FQBN="arduino:renesas_uno:unor4wifi"

"$ROOT/scripts/gen-arduino-secrets.sh"

PORT="${1:-}"
if [[ -z "$PORT" ]]; then
  PORT=$(arduino-cli board list --format json 2>/dev/null \
    | python3 -c 'import sys,json;[print(p["port"]["address"]) for p in json.load(sys.stdin) if "renesas" in json.dumps(p).lower() or "uno" in json.dumps(p).lower()]' \
    | head -1)
fi
[[ -z "$PORT" ]] && { echo "no UNO R4 detected; pass the port explicitly: ./scripts/flash.sh /dev/cu.usbmodemXXXX" >&2; exit 1; }

echo "compiling ($FQBN) ..."
arduino-cli compile --fqbn "$FQBN" "$SKETCH"
echo "uploading to $PORT ..."
arduino-cli upload -p "$PORT" --fqbn "$FQBN" "$SKETCH"
echo "done. Open serial: arduino-cli monitor -p $PORT -c baudrate=115200"
