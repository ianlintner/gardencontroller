import importlib.util
from pathlib import Path

def load_garden():
    path = Path(__file__).resolve().parent.parent / "garden.py"
    spec = importlib.util.spec_from_file_location("garden", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_version():
    assert load_garden().__version__ == "0.1.0"
