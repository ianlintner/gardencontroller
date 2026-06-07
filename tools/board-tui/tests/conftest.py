import sys
from pathlib import Path

# Make the board-tui modules importable as top-level (frames, sources, render).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
