"""骨架冒烟：包可导入、CLI 可调、arch 边界可执行。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_package_import() -> None:
    import agentplatform

    assert agentplatform.__version__ == "0.1.0"


def test_cli_version() -> None:
    from agentplatform.cli import main

    assert main(["version"]) == 0


def test_arch_check_runs() -> None:
    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "arch_check.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
