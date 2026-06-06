import importlib.util
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]  # irrigation/


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def core():
    return _load("garden_core", "garden_core.py")


@pytest.fixture
def gateway(core):
    return _load("gateway", "mcp/gateway.py")
