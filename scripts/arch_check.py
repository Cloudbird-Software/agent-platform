#!/usr/bin/env python3
"""依赖边界检查（Makefile arch 目标，ADR-0025）。

核心层（spec/render/flow/governance/drift/observe）禁止 import 上游运行时
（openjiuwen/jiuwenswarm）——上游符号只允许出现在 adapter/ 与 bootstrap/。
这保证：CI 单测零上游依赖即可全绿；上游升级的影响面被物理隔离。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

FORBIDDEN = ("openjiuwen", "jiuwenswarm", "workswarm")
ALLOWED_PREFIXES = ("adapter", "bootstrap")

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "agentplatform"

violations: list[str] = []

for py in sorted(SRC.rglob("*.py")):
    rel = py.relative_to(SRC)
    parts = rel.parts
    if parts[0] not in ALLOWED_PREFIXES:
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in FORBIDDEN:
                    violations.append(f"{rel}: import {name}（核心层禁上游）")

if violations:
    print("ARCH CHECK FAIL:", file=sys.stderr)
    for v in violations:
        print(f"  {v}", file=sys.stderr)
    sys.exit(1)

print(f"ARCH OK: 核心层 {len(list(SRC.rglob('*.py')))} 文件零上游依赖")
