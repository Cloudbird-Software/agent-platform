"""drift/：一致性检查与漂移监控（ADR-0025）。

三类漂移（声明面 ↔ 输出面 ↔ 磁盘现实）：
- spec drift   声明变了没重渲染（manifest.spec_digest != 当前快照摘要）
- file drift   渲染产物被手改（磁盘哈希 != manifest 记录）
- orphan       workspace 出现 manifest 未记录的文件（注入/孤儿）

watch：周期对账，漂移事件 JSONL 输出（PR-7 observe 的数据源之一）。
"""

from agentplatform.drift.checker import DriftIssue, DriftReport, check_workspace

__all__ = ["DriftIssue", "DriftReport", "check_workspace"]
