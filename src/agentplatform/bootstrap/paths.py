"""paths：默认声明源解析——vendor 快照让 clone 后零参数 init。

优先级：
  1. $AGENTPLATFORM_REGISTRY（显式覆盖）
  2. 包安装位置向上找 vendor/agent-registry（源码 checkout / Docker /app）
  3. CWD/vendor/agent-registry（在仓内任意子目录执行时）
"""

from __future__ import annotations

import os
from pathlib import Path

VENDOR_DIR = "vendor/agent-registry"


def default_registry() -> Path | None:
    """返回可用的默认 registry 路径；无则 None（调用方报错并给 fix 提示）。"""
    env = os.environ.get("AGENTPLATFORM_REGISTRY")
    if env:
        p = Path(env)
        if (p / "registry").is_dir():
            return p
        # 显式指定但不可用——直接失败比静默回落更诚实
        raise FileNotFoundError(f"$AGENTPLATFORM_REGISTRY={env} 无 registry/ 目录")

    here = Path(__file__).resolve()
    for base in here.parents[:6]:  # src/agentplatform/bootstrap → 仓根/镜像根
        cand = base / VENDOR_DIR
        if (cand / "registry").is_dir():
            return cand
    cwd_cand = Path.cwd() / VENDOR_DIR
    if (cwd_cand / "registry").is_dir():
        return cwd_cand
    return None
