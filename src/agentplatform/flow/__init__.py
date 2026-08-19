"""flow/：声明相位状态机 → SwarmFlow 编译（ADR-0025）。

三层：
- graph   —— standards/team-collaboration.yaml#flow.phases → PhaseGraph IR + 图验证
             （deadlock_check 声明的可执行化：专属出边/可达性/悬空事件）
- codegen —— PhaseGraph + team 声明 → SwarmFlow 脚本（META + async def run(args)，
             确定性输出：同 spec_digest → 字节级相同）
- dryrun  —— 生成脚本的静态校验（ast 级：算子白名单/危险 import/相位标题/结构）

机制原型的落地形态（声明适配，ADR-0025）：SwarmFlow 无独立"机制"执行体，
机制 = 脚本内确定性函数（verdict 合并/预算收束）+ schema 约束的 worker 结构化输出
——LLM 只产结构化事实，pass/fail 由确定性代码计算（mechanism 无 LLM 决定权）。
"""

from agentplatform.flow.codegen import compile_team_flow, flow_outputs
from agentplatform.flow.dryrun import dryrun_registry, lint_workflow_source
from agentplatform.flow.graph import (
    Edge,
    PhaseGraph,
    ValidationIssue,
    load_phase_graph,
    validate_graph,
)

__all__ = [
    "Edge",
    "PhaseGraph",
    "ValidationIssue",
    "compile_team_flow",
    "dryrun_registry",
    "flow_outputs",
    "lint_workflow_source",
    "load_phase_graph",
    "validate_graph",
]
