"""Static checks over the console's shipped assets: the console has no build step to catch them."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from thymira.web.app import STATIC_DIR

if TYPE_CHECKING:
    from pathlib import Path

NODE = shutil.which("node") or ""
IMPORT_RE = re.compile(r"""from\s+["'](\.{1,2}/[^"']+)["']""")
ASSET_RE = re.compile(r"""(?:href|src)=["']/static/([^"']+)["']""")
MODULES = sorted(STATIC_DIR.glob("js/**/*.js"))


def test_every_relative_import_resolves_to_a_shipped_module() -> None:
    missing = [
        f"{module.relative_to(STATIC_DIR)} -> {spec}"
        for module in MODULES
        for spec in IMPORT_RE.findall(module.read_text(encoding="utf-8"))
        if not (module.parent / spec).resolve().is_file()
    ]
    assert missing == []


def test_the_shell_references_only_shipped_assets() -> None:
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    missing = [asset for asset in ASSET_RE.findall(html) if not (STATIC_DIR / asset).is_file()]
    assert missing == []
    assert "console.css" not in html


@pytest.mark.parametrize("module", MODULES, ids=lambda path: str(path.relative_to(STATIC_DIR)))
@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_every_module_parses(module: Path) -> None:
    result = subprocess.run(
        [NODE, "--check", str(module)],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
