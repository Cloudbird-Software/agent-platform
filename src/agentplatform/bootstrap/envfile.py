"""envfile：manifest.env_refs → .env.example（配 API 的唯一手工步骤）。

只生成模板不写 .env 本体（不覆盖用户凭据）；重复生成幂等（.env.example
是渲染说明的一部分，可安全重建——.env 本体永不被工具触碰）。

预填策略（ADR-0025 开箱即用）：
- 凭据类（LLM Gateway 两变量）留空必填——用户唯一要手工填的东西；
- 非敏感基础设施变量预填本地默认（sqlite 路径/工作区目录/OTEL 关闭），
  cp 后即可用；生产姿态覆盖默认值即可。
- 未知变量留空并标"必填"——宁可阻断也不猜默认（fail-closed）。
"""

from __future__ import annotations

from pathlib import Path

from agentplatform.render.manifest import load_manifest

HEADER = """\
# ============================================================================
# agent-platform 环境模板（由 manifest.env_refs 生成——ap envfile）
# 用法：cp .env.example .env && 只填【必填】项（其余已预填本地默认，可覆盖）。
# .env 已在 .gitignore；凭据只经环境变量注入，绝不入声明/渲染产物/账本。
# ============================================================================
"""

# 必填凭据：精确圈定，不做 KEY/TOKEN 启发式（猜错比阻断更贵）
_MUST_FILL = frozenset({"LLM_GATEWAY_KEY", "LLM_GATEWAY_ENDPOINT"})

# 已知非敏感默认（本地单机姿态）
_STATIC_DEFAULTS = {
    "OTEL_ENABLED": "false",
    "OTEL_ENDPOINT": "http://127.0.0.1:4318",  # OTEL_ENABLED=false 时不使用
}


def default_for(var: str) -> str | None:
    """返回变量本地默认值；无已知默认（凭据/未知）返回 None。"""
    if var in _MUST_FILL:
        return None
    if var in _STATIC_DEFAULTS:
        return _STATIC_DEFAULTS[var]
    if var.endswith("_DB_DSN"):
        # sqlite 库文件相对路径（声明 storage.type: sqlite——相对 agent_teams_home 解析）
        return f"data/{var[: -len('_DB_DSN')].lower()}.db"
    if var.endswith("_WORKSPACE_ROOT"):
        return f"data/{var[: -len('_WORKSPACE_ROOT')].lower()}-ws"
    return None


def render_envfile(workspace: str | Path) -> str:
    """渲染 .env.example 文本（不落盘——init/CLI 决定写哪里）。"""
    m = load_manifest(Path(workspace))
    lines = [HEADER]
    if not m.env_refs:
        lines.append("# 本渲染产物不引用任何环境变量（无需配置）\n")
        return "\n".join(lines)
    for var in m.env_refs:
        d = default_for(var)
        if d is None:
            lines.append(f"# {var} ——【必填】{'网关鉴权' if var in _MUST_FILL else '无已知本地默认'}")
            lines.append(f"{var}=\n")
        else:
            lines.append(f"# {var} —— 预填本地默认（生产可覆盖）")
            lines.append(f"{var}={d}\n")
    return "\n".join(lines)
