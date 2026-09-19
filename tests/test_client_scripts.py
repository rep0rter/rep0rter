"""The published scripts are generated from client/src and must stay classic scripts."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "rep0rter" / "templates"
SOURCES = ROOT / "client" / "src"

SCRIPTS = sorted(path.name for path in TEMPLATES.glob("*.js"))


def test_every_published_script_has_a_typescript_source():
    """A hand-edited .js would be silently overwritten by the next build."""
    assert SCRIPTS
    for name in SCRIPTS:
        assert (SOURCES / name.replace(".js", ".ts")).exists(), f"{name} has no source in client/src"


@pytest.mark.parametrize("name", SCRIPTS)
def test_published_scripts_are_classic_scripts(name):
    """index.html loads these with a plain <script src>, and theme.js runs before
    first paint. A stray import or export would turn the file into an ES module
    and stop it executing at all."""
    source = (TEMPLATES / name).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(?:import|export)\b", source, re.MULTILINE)


def test_the_toolchain_is_pinned():
    """An unpinned compiler would change published bytes without a code change."""
    package = (ROOT / "client" / "package.json").read_text(encoding="utf-8")
    assert re.search(r'"typescript":\s*"\d+\.\d+\.\d+"', package), "typescript must be pinned exactly"
