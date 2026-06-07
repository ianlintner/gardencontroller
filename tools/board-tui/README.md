# board-tui — live garden board monitor

Live terminal dashboard of the garden board's USB serial telemetry: all analog
pins (A0–A5), translated soil %, temp/humidity, and board/network metrics.

## Install
    cd tools/board-tui && pip install -r requirements.txt

## Run
    python board_tui.py                 # auto-detect the board's serial port
    python board_tui.py --port /dev/tty.usbmodem1101
    python board_tui.py --simulate      # no hardware (demo/test)
    python board_tui.py --replay cap.ndjson
    python board_tui.py --record cap.ndjson   # save a live session

Close the Arduino IDE Serial Monitor first — the USB serial port is single-owner.
Requires board firmware >= 1.0.1 (emits the NDJSON telemetry stream).
