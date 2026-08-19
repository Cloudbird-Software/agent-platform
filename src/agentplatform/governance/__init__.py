"""governance/：治理执行面（ADR-0025）——机制原型的可执行内核。

四个纯 Python 部件（零上游依赖——adapter 层把它们装上 jiuwenswarm rails）：
- ledger    事件哈希链（trace-archive 的防篡改载体——声明：证据/事件流不随队消失）
- cardgate  卡门（fields_required 校验 + review dispatch 表 + 卡生命周期状态机）
- budget    三层预算（envelope/per_card/overhead）+ 升级通道永不冻结不变式
- writelock 通道 ACL + artifact 写锁（任一时刻同 artifact active 座位 <= 1）
"""

from agentplatform.governance.budget import BudgetGovernor, BudgetResult
from agentplatform.governance.cardgate import CardGate, CardStateError, ReviewDispatch
from agentplatform.governance.ledger import EventLedger, LedgerIntegrityError
from agentplatform.governance.writelock import AclTable, WriteLock, WriteLockError

__all__ = [
    "AclTable",
    "BudgetGovernor",
    "BudgetResult",
    "CardGate",
    "CardStateError",
    "EventLedger",
    "LedgerIntegrityError",
    "ReviewDispatch",
    "WriteLock",
    "WriteLockError",
]
