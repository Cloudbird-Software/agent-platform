"""agentplatform——agent-registry 声明到 openjiuwen 运行时的渲染与治理执行层。

分层（ADR-0025）：
- spec/      声明加载（registry 快照 → 内存视图）
- render/    渲染器（声明 → jiuwenswarm workspace/config，幂等 + 指纹）
- flow/      SwarmFlow workflow 编译器（相位图 → 确定性脚本）
- governance/ 机制原型执行面（事件哈希链/卡门/预算/写锁）
- drift/     渲染一致性与漂移监控
- observe/   TUI 可观测 + agentctl 干预命令
- adapter/   上游扩展包（rails/AgentBackend；唯一允许 import 上游的层）
"""

__version__ = "0.1.0"
