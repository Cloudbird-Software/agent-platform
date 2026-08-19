---
name: code-review-fix
version: 1.0.0
author: cloudbird
description: 处理 PR 检视意见：逐条读取、修改代码、回复讨论、直至 CI 绿
tags: [vcs, review]
allowed_tools: [tool:read_file, tool:write_file, tool:bash, tool:gitcode-pr]
requires: [skill:tdd-loop]
status: approved
acceptance:
  - "[ci] 全部检视线已 resolve 或回复"
  - "[ci] gate 全绿"
  - "[llm] 每条意见的修改与意见本身对应，无静默忽略"
---

# PR 检视意见处理

1. 拉取 PR 全部 review threads（含未 resolve）。
2. 逐条分类：采纳 / 质疑（需回复说明理由）。
3. 采纳的按 tdd-loop 修改：先补失败测试，再改实现。
4. 每条处理完即回复 thread；质疑的必须给出依据（benchmark/文档/风险分析）。
5. 禁止 force-push 重写他人已审历史；追加提交。
6. 最终确认 gate 绿，@评审者复检。
