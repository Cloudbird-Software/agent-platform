"""BudgetAdmission：治理预算 → 引擎并发闸（AgentAdmission Protocol 实现）。

上游契约（openjiuwen/agent_teams/workflow/engine/admission.py）：
    class AgentAdmission(Protocol):
        @asynccontextmanager
        async def acquire(self) -> AsyncIterator[None]: ...

本实现：acquire 时检查治理面——wave 冻结（envelope 熔断）→ 拒绝入闸
（raise BudgetFrozenError，工作流解栈不记 journal）；放行时落
agent.admitted 事件（label 归因，TUI 可见）。

注意：不 import 上游（Protocol 是结构化的——鸠类型即可），单测零上游依赖。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from agentplatform.observe.store import RuntimeStore


class BudgetFrozenError(Exception):
    """wave 已冻结（team_envelope 熔断）——agent 调用被治理面拒绝。"""


class BudgetAdmission:
    """每次 agent() 调用经此闸：冻结拒绝 / 放行记账。"""

    def __init__(self, store: RuntimeStore, *, card_id: str | None = None) -> None:
        self._store = store
        self._card_id = card_id
        self.admitted = 0
        self.rejected = 0

    @asynccontextmanager
    async def acquire(self, label: str | None = None) -> AsyncIterator[None]:
        """上游 engine 在每次 agent() 调用前进入本上下文。

        label 参数是超集扩展：上游调用 acquire() 无参——label 仅在
        引擎侧显式传时归因（不破坏 Protocol 结构匹配）。
        """
        if self._store.budget.frozen:
            self.rejected += 1
            reason = self._store.snapshot()["budget"]["freeze_reason"]
            raise BudgetFrozenError(f"agent 入闸被拒（wave 冻结）：{reason}")
        self.admitted += 1
        self._store.ledger.append(
            "agent.admitted",
            "mechanism:admission",
            {"label": label} if label else {},
            card_id=self._card_id,
        )
        self._store.flush()
        try:
            yield
        finally:
            self._store.ledger.append("agent.released", "mechanism:admission", {}, card_id=self._card_id)
            self._store.flush()

    # 引擎有时以无参调用（rt.make_cap 之外的路径）——保证签名兼容
    def __call__(self, *_: Any, **__: Any) -> Any:
        return self.acquire()
