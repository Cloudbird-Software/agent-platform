"""adapter/：治理执行面 ↔ openjiuwen 运行时的桥接层（ADR-0025）。

架构约束（scripts/arch_check.py 强制）：
- 核心层（spec/render/flow/governance/drift/observe）禁止 import 上游；
- 上游符号只允许出现在本包与 bootstrap/——升级影响面物理隔离。

对接面（上游 run_swarmflow 的三个治理挂点 + 工具轨道）：
- gate.py          BudgetAdmission：AgentAdmission Protocol 实现——
                   预算冻结即拒绝入闸（治理三层预算 → 引擎并发闸）
- observer.py      LedgerObserver：WorkflowObserver 包装——引擎进度事件
                   桥到治理账本（wave.phase/agent.* 可观测+可审计）
- rails.py         ToolRails：capabilities.allow 声明 → 工具白名单轨道
                   （fail-closed：无匹配即拒绝）
- modelresolver.py GatewayModelResolver：model alias → 网关配置
                   （alias 解析只发生在 LLM Gateway——ADR-0002）
- runner.py        run_team_flow：装配入口（唯一 import 上游处，lazy）
"""

from agentplatform.adapter.gate import BudgetAdmission, BudgetFrozenError
from agentplatform.adapter.modelresolver import GatewayModelResolver
from agentplatform.adapter.observer import LedgerObserver
from agentplatform.adapter.rails import ToolRails

__all__ = [
    "BudgetAdmission",
    "BudgetFrozenError",
    "GatewayModelResolver",
    "LedgerObserver",
    "ToolRails",
]
