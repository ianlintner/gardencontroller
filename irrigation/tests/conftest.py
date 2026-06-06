import importlib.util
import sys
from pathlib import Path
import pytest


def _load(name, filename):
    path = Path(__file__).resolve().parent.parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # so dependents' `from <name> import ...` resolve
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def core():
    return _load("garden_core", "garden_core.py")


@pytest.fixture
def garden():
    _load("garden_core", "garden_core.py")  # register before garden.py imports it
    return _load("garden", "garden.py")
