"""runner：run_team_flow——渲染产物 + 治理面 + 上游 run_swarmflow 的装配入口。

唯一 import 上游处（lazy——本模块被调用时 runtime 依赖必须已装：
runtime/requirements.lock 钉版安装）。装配三挂点：
    agent_gate=BudgetAdmission   预算熔断 → 引擎入闸拒绝
    observer=LedgerObserver      引擎进度 → 治理账本（TUI 可见）
    model_resolver=GatewayModelResolver  alias → 网关凭据（ADR-0002）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentplatform.adapter.gate import BudgetAdmission
from agentplatform.adapter.modelresolver import GatewayModelResolver
from agentplatform.adapter.observer import LedgerObserver
from agentplatform.observe.store import RuntimeStore
from agentplatform.render.manifest import load_manifest


class RunnerError(Exception):
    """装配失败（workspace 未渲染/脚本缺失/上游不可用）。"""


def _load_models_config(workspace: Path) -> dict[str, dict]:
    """workspace/models.json → resolver 注册表（渲染面机器可读模型表）。"""
    from agentplatform.adapter.modelresolver import load_models_registry

    return load_models_registry(workspace)


def resolve_flow_script(workspace: Path, team_id: str) -> Path:
    """swarmflow/<team_id>.py（渲染 include_flows 产物）。"""
    p = workspace / "swarmflow" / f"{team_id}.py"
    if not p.is_file():
        raise RunnerError(f"{p} 不存在（先 render --registry ... --out workspace）")
    return p


async def run_team_flow(
    workspace: str | Path,
    state_dir: str | Path,
    team_id: str,
    *,
    model: Any,
    args: Any = None,
    card_id: str | None = None,
    session_id: str | None = None,
    abort_event: Any = None,
) -> Any:
    """执行团队的 SwarmFlow：治理挂点全接（预算闸/事件桥/模型解析）。

    model：默认 LLM 对象（上游 Model 实例——worker 无 model hint 时用）。
    返回：脚本 run(args) 的返回值。
    """
    try:
        from openjiuwen.agent_teams.workflow.runner import run_swarmflow
        from openjiuwen.agent_teams.workflow.schema import TeamModelConfig  # noqa: F401
    except ImportError as e:
        raise RunnerError(f"上游运行时不可用：{e}（安装 runtime/requirements.lock 到执行环境）") from e

    ws = Path(workspace)
    load_manifest(ws)  # 完整性：manifest 自校验（渲染面可信才执行）
    script = resolve_flow_script(ws, team_id)

    store = RuntimeStore.open(state_dir)
    resolver = GatewayModelResolver(_load_models_config(ws))
    raw_resolve = resolver.as_callable()

    def model_resolver(alias: str):
        d = raw_resolve(alias)
        return _build_team_model_config(d)

    observer = LedgerObserver(store, card_id=card_id)
    admission = BudgetAdmission(store, card_id=card_id)

    store.ledger.append(
        "wave.flow_started",
        "mechanism:runner",
        {"team": team_id, "script": str(script)},
        card_id=card_id,
    )
    store.flush()
    try:
        result = await run_swarmflow(
            str(script),
            model=model,
            observer=_UpstreamObserver(observer),
            args=args,
            team_name=team_id,
            model_resolver=model_resolver,
            agent_gate=admission,
            session_id=session_id,
            abort_event=abort_event,
        )
    finally:
        store.ledger.append("wave.flow_finished", "mechanism:runner", {"team": team_id}, card_id=card_id)
        store.flush()
    return result


def _build_team_model_config(d: dict) -> Any:
    """resolver dict → 上游 TeamModelConfig（lazy import 处构造）。"""
    from openjiuwen.agent_teams.schema.deep_agent_spec import TeamModelConfig
    from openjiuwen.core.foundation.llm.schema.config import (
        ModelClientConfig,
        ModelRequestConfig,
    )

    return TeamModelConfig(
        model_client_config=ModelClientConfig(**d["model_client_config"]),
        model_request_config=ModelRequestConfig(**d["model_request_config"]),
    )


class _UpstreamObserver:
    """LedgerObserver → 上游 WorkflowObserver 同构包装（emit/events/run）。

    引擎侧 progress_sink 只要求 emit；events/run 供 launcher 读取。
    """

    def __init__(self, inner: LedgerObserver) -> None:
        self._inner = inner

    def emit(self, event: Any) -> None:
        self._inner.emit(event)

    @property
    def events(self) -> list[Any]:
        return self._inner.events

    @property
    def run(self) -> Any:
        return self._inner.run
