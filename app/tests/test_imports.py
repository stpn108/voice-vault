"""
Import smoke test: every top-level module must import without error.

Catches NameError at module level, circular imports and missing
dependencies before they surface as a crash in production (template decision T-001).
"""
import importlib
import pathlib

import pytest

APP_DIR = pathlib.Path(__file__).resolve().parent.parent
MODULES = sorted(
    p.stem for p in APP_DIR.glob("*.py") if not p.name.startswith("test_")
)


@pytest.mark.parametrize("module_name", MODULES)
def test_module_imports(module_name):
    importlib.import_module(module_name)
