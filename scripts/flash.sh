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
  # `board list --format json` returns {"detected_ports":[...]}. Prefer a port
  # whose matching board fqbn is the R4; fall back to a renesas/uno match.
  PORT=$(arduino-cli board list --format json 2>/dev/null | python3 -c '
import sys, json
ports = json.load(sys.stdin).get("detected_ports", [])
for p in ports:
    if any("renesas_uno" in (b.get("fqbn") or "") for b in (p.get("matching_boards") or [])):
        print(p["port"]["address"]); break
else:
    for p in ports:
        if "renesas" in json.dumps(p).lower() or "uno r4" in json.dumps(p).lower():
            print(p["port"]["address"]); break
')
fi
[[ -z "$PORT" ]] && { echo "no UNO R4 detected; pass the port explicitly: ./scripts/flash.sh /dev/cu.usbmodemXXXX" >&2; exit 1; }

echo "compiling ($FQBN) ..."
arduino-cli compile --fqbn "$FQBN" "$SKETCH"
echo "uploading to $PORT ..."
arduino-cli upload -p "$PORT" --fqbn "$FQBN" "$SKETCH"
echo "done. Open serial: arduino-cli monitor -p $PORT -c baudrate=115200"
