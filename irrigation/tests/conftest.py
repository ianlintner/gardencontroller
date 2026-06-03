import importlib.util
from pathlib import Path
import pytest

@pytest.fixture
def garden():
    path = Path(__file__).resolve().parent.parent / "garden.py"
    spec = importlib.util.spec_from_file_location("garden", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
