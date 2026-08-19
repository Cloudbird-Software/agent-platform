"""envfile：manifest.env_refs → .env.example（配 API 的唯一手工步骤）。

只生成模板不写 .env 本体（不覆盖用户凭据）；重复生成幂等（.env.example
是渲染说明的一部分，可安全重建——.env 本体永不被工具触碰）。
"""

from __future__ import annotations

from pathlib import Path

from agentplatform.render.manifest import load_manifest

HEADER = """\
# ============================================================================
# agent-platform 凭据模板（由 manifest.env_refs 生成——ap envfile）
# 用法：cp .env.example .env && 填入真实值。.env 已在 .gitignore。
# 硬规则：凭据只经环境变量注入，绝不入声明/渲染产物/账本（ADR-0025 泄漏扫描）。
# ============================================================================
"""


def render_envfile(workspace: str | Path) -> str:
    """渲染 .env.example 文本（不落盘——init/CLI 决定写哪里）。"""
    m = load_manifest(Path(workspace))
    lines = [HEADER]
    if not m.env_refs:
        lines.append("# 本渲染产物不引用任何环境变量（无需配置）\n")
        return "\n".join(lines)
    for var in m.env_refs:
        lines.append(f"# {var}")
        lines.append(f"{var}=\n")
    return "\n".join(lines)
