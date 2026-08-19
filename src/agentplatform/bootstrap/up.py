"""up：执行团队的 SwarmFlow——配好 API 后的“就能用”入口。

两档：
  --dry-run  预检（零上游依赖）：manifest 自校验 + 脚本在位 + 全模型 alias
             凭据可解析。不发起任何 LLM 调用。
  live       经 adapter/runner 全挂点执行（预算闸/事件桥/模型解析）；
             需要 runtime/requirements.lock 已安装。

.env 语义：workspace/.env 在启动时注入 os.environ（只补缺不覆盖）。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from agentplatform.adapter.modelresolver import (
    GatewayModelResolver,
    ModelResolutionError,
    load_models_registry,
)
from agentplatform.bootstrap.dotenv import apply_env
from agentplatform.render.manifest import load_manifest


class UpError(Exception):
    """up 前置条件不满足（可修复——消息里带 fix 动作）。"""


def _resolver(workspace: Path) -> GatewayModelResolver:
    try:
        return GatewayModelResolver(load_models_registry(workspace))
    except ModelResolutionError as e:
        raise UpError(str(e)) from e


def _preflight(workspace: Path, team: str) -> dict[str, Any]:
    """零调用预检：渲染面完整 + 脚本在位 + 凭据齐备。返回检查明细。"""
    m = load_manifest(workspace)  # 自校验（被篡改即 ValueError）
    script = workspace / "swarmflow" / f"{team}.py"
    if not script.is_file():
        available = sorted(p.stem for p in (workspace / "swarmflow").glob("*.py"))
        raise UpError(f"swarmflow/{team}.py 不存在（可用：{available or '无'}）")

    resolver = _resolver(workspace)
    resolved, missing = [], []
    for alias in sorted(resolver._models):
        try:
            resolver.resolve(alias)
            resolved.append(alias)
        except ModelResolutionError as e:
            missing.append(str(e))
    return {
        "spec_digest": m.spec_digest[:16],
        "script": str(script),
        "models_ok": resolved,
        "models_missing": missing,
    }


def run_up(
    workspace: str | Path,
    team: str,
    *,
    state: str | Path | None = None,
    dry_run: bool = False,
    model: str | None = None,
    args_json: str | None = None,
) -> dict[str, Any]:
    """执行（或预检）团队流；返回摘要 dict（CLI 负责 JSON 打印）。"""
    ws = Path(workspace)
    applied = apply_env(ws / ".env")
    report = _preflight(ws, team)

    if dry_run:
        ok = not report["models_missing"]
        return {"mode": "dry-run", "team": team, "ok": ok, "env_applied": applied, **report}

    if report["models_missing"]:
        raise UpError("凭据缺失（填 " + str(ws / ".env") + "）：" + "; ".join(report["models_missing"]))

    state_dir = Path(state) if state else ws / "state"
    if not (state_dir / "ledger.jsonl").is_file():
        raise UpError(f"state 未初始化：{state_dir}（先 ap init）")

    from agentplatform.adapter.runner import run_team_flow

    resolver = _resolver(ws)
    default_alias = json.loads((ws / "models.json").read_text(encoding="utf-8")).get("default")
    alias = model or default_alias or report["models_ok"][0]
    d = resolver.as_callable()(alias)

    from openjiuwen.core.foundation.llm.model import Model
    from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig

    default_model = Model(
        ModelClientConfig(**d["model_client_config"]),
        ModelRequestConfig(**d["model_request_config"]),
    )
    flow_args = json.loads(args_json) if args_json else None

    result = asyncio.run(
        run_team_flow(
            ws,
            state_dir,
            team,
            model=default_model,
            args=flow_args,
        )
    )
    return {
        "mode": "live",
        "team": team,
        "model": alias,
        "env_applied": applied,
        "spec_digest": report["spec_digest"],
        "result": result,
    }
