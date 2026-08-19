---
name: tdd-loop
version: 1.0.0
author: cloudbird
description: 测试驱动开发循环：先写失败测试，再实现，再重构，每轮跑 gate
tags: [dev, testing]
allowed_tools: [tool:read_file, tool:write_file, tool:bash]
status: approved
acceptance:
  - "[ci] 新增/修改代码 100% 有关联测试（coverage diff 不下降）"
  - "[ci] gate 全绿（lint+arch+test+hygiene）"
  - "[llm] 提交说明能对应到某条失败测试先行"
---

# TDD 循环

当本技能被选用时，严格按以下顺序执行，不允许跳步：

1. **Red**：写一条会失败的最小测试，运行确认失败（记录失败输出）。
2. **Green**：写刚好让测试通过的最小实现，运行确认通过。
3. **Refactor**：消除重复；每次重构后测试必须保持绿。
4. 循环直至任务完成；最终本地跑一次完整 gate 再提 PR。

约束：
- 禁止先写实现后补测试。
- 测试选择遵循 governance/policy/testing.yaml（单测/性质/golden）。
