"""observe/：TUI 可观测性 + agentctl 干预命令面（ADR-0025）。

架构：一切状态 = 事件流投影（事件溯源——与声明的 simulate-wave 方法论同构）。
- projection  账本事件 → 只读状态视图（TUI/查询命令共用）
- store       RuntimeStore：workspace/state/ 文件面（ledger.jsonl + budget.json）
              治理对象经事件重放恢复（reattach 真账本后继续增量 append）
- tui         ANSI 仪表盘（纯标准库——零三方依赖）
- agentctl    干预动词表（JSON 输出，agent 可直接调用）
"""

from agentplatform.observe.projection import project
from agentplatform.observe.store import RuntimeStore

__all__ = ["RuntimeStore", "project"]
