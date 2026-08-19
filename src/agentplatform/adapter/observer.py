"""LedgerObserver：引擎进度事件 → 治理账本（可观测 + 可审计桥）。

上游契约（openjiuwen/agent_teams/workflow/observer.py）：
    WorkflowObserver(on_event)  emit(WorkflowProgressEvent)  events  run

本实现：透传构造上游 WorkflowObserver 不必要——直接提供同构 emit/events/run
鸭子类型（引擎只调 emit；run 的 4 层折叠是 UI 消费，TUI 走治理投影，不复用）。

事件映射（引擎 kind → 账本 kind）：
    workflow_started → wave.run_started（phases 静态计划入 payload）
    phase            → wave.phase（相位推进——与声明相位图对账的锚点）
    agent_started    → agent.started（label/model 归因）
    agent_completed  → agent.completed（outcome 预览）
    agent_failed     → agent.failed（message）
    workflow_completed/failed → wave.run_ended
"""

from __future__ import annotations

from typing import Any

from agentplatform.observe.store import RuntimeStore

_KindMap = {
    "workflow_started": "wave.run_started",
    "phase": "wave.phase",
    "agent_started": "agent.started",
    "agent_completed": "agent.completed",
    "agent_failed": "agent.failed",
    "workflow_completed": "wave.run_ended",
    "workflow_failed": "wave.run_ended",
}


def _scalar(v: Any) -> Any:
    """账本只收 JSON 标量；结构体（如 PhasePlan）取 title/str 预览。

    账本序列化严格（确定性哈希链），对象直入会 TypeError 崩观测面——
    观测桥必须收敛到标量，深度结构属于日志层不属于账本层。
    """
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    t = getattr(v, "title", None)
    if isinstance(t, str):
        return t
    return str(v)[:120]


class LedgerObserver:
    """emit(event)：账本事件 + 内存累积（events/run 供引擎侧读取）。"""

    def __init__(self, store: RuntimeStore, *, card_id: str | None = None) -> None:
        self._store = store
        self._card_id = card_id
        self._events: list[Any] = []

    def emit(self, event: Any) -> None:
        """引擎 progress_sink；事件体尽量精简（账本不是日志倾倒场）。"""
        self._events.append(event)
        kind = getattr(event, "kind", "?")
        ledger_kind = _KindMap.get(kind)
        if ledger_kind is None:
            return  # human_prompt/human_replied/log 不入账本（人机交互层）
        payload: dict[str, Any] = {}
        for f in ("phase", "label", "model", "name"):
            v = getattr(event, f, None)
            if v is not None:
                payload[f] = _scalar(v)
        if kind in ("agent_completed", "agent_failed", "workflow_failed"):
            msg = getattr(event, "message", None) or getattr(event, "outcome", None)
            if msg:
                payload["detail"] = str(msg)[:200]
        if kind == "workflow_started":
            # 引擎把 META.phases 规范化为 PhasePlan 对象列表——账本只收标量标题
            payload["phases"] = [
                _scalar(p) for p in (getattr(event, "phases", None) or [])
            ]
        self._store.ledger.append(ledger_kind, "mechanism:engine", payload, card_id=self._card_id)
        self._store.flush()

    @property
    def events(self) -> list[Any]:
        return list(self._events)

    @property
    def run(self) -> dict[str, Any]:
        """引擎 observer.run 的治理版：事件统计摘要（TUI/审计用）。"""
        counts: dict[str, int] = {}
        for e in self._events:
            counts[getattr(e, "kind", "?")] = counts.get(getattr(e, "kind", "?"), 0) + 1
        return {"events": len(self._events), "by_kind": counts}
