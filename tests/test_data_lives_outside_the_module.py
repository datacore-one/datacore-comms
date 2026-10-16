"""MEM-63: the comms module keeps no user data inside itself.

Watchlists, brand angles and human feedback live in the selected space's
private module-data folder, resolved through the core module_context helper.
"""
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE / "lib"))
sys.path.insert(0, str(MODULE.parents[1] / "lib"))

import comms_paths  # noqa: E402


@pytest.fixture
def install(tmp_path, monkeypatch):
    root = tmp_path / "Data"
    space = root / "0-personal"
    (space / ".datacore").mkdir(parents=True)
    (space / ".datacore/config.yaml").write_text("space: {name: personal, type: personal}\n")
    other = root / "2-other/.datacore"
    other.mkdir(parents=True)
    (other / "config.yaml").write_text("space: {name: other, type: personal}\n")
    monkeypatch.setenv("DATACORE_ROOT", str(root))
    monkeypatch.delenv("DATACORE_SPACE", raising=False)
    return root, space


def test_data_resolves_to_the_private_space_folder(install):
    _, space = install
    data = comms_paths.data_dir()
    # A private folder of the space (module-data/, or the space's own scoped
    # modules/ folder on a fresh install) -- never the installed module.
    assert data.is_relative_to(space / ".datacore") and not data.is_relative_to(MODULE)


def test_the_content_engine_reads_angles_from_the_private_folder(install, monkeypatch):
    import content_engine
    _, space = install
    data = comms_paths.data_dir()
    (data / "angles-plur.yaml").write_text("angles:\n  - {name: memory}\n")
    monkeypatch.setattr(content_engine, "COMMS_DATA", None)
    assert content_engine.load_angles("plur") == [{"name": "memory"}]


def test_the_content_engine_names_no_module_data_folder():
    source = (MODULE / "lib/content_engine.py").read_text()
    assert 'MODULE_DIR / "data"' not in source
