"""dotenv：极简 .env 读取（KEY=VALUE，# 注释）——零三方依赖。

apply_env 只补缺不覆盖：进程已显式注入的值优先于文件（CI/容器场景）。
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def apply_env(path: Path, *, override: bool = False) -> list[str]:
    """把 .env 注入 os.environ；返回本次实际生效的变量名列表。"""
    applied: list[str] = []
    for k, v in load_env_file(path).items():
        if override or k not in os.environ:
            os.environ[k] = v
            applied.append(k)
    return applied
