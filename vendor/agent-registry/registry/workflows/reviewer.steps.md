# reviewer（test-author）内部流程

固定流程，不允许跳步（workflow.mode: fixed）：

1. **装载**：读工作卡 + owner 已批验收示例 + 测试规格（hermetic：不见 builder 草稿）。
2. **作者权**：验收示例→acceptance 测试转译（仅 `tests/acceptance/**`）；每条测试注明对应验收示例 id；spec 覆盖不足 → 记"规格缺口"意见，不替 planner 补规格。
3. **冻结**：实现开始前完成测试树并回写 test_tree_sha 到卡——冻结后判卷阶段零介入。
4. **执行**：本地跑测试验证可执行/无环境依赖（只读执行，不改实现）。
5. **变更纪律**：减弱型变更（删测试/删断言/放宽/skip）一律走特权卡（新卡+owner 批+test_weakening 事件），本流程内禁止。
6. **回流**：规格歧义 → 标记上报（judge/owner）；测试被 mutation 打低分 → **只记 test_gap/mutation finding，不动冻结树**；修改测试须新卡+owner 批+重新冻结（同减弱纪律——CodeRabbit #5 修正：原"主动修测试"违反冻结）。
（判卷不在此流程内——verdict 由 verifier 机制对冻结树运行产出。）
