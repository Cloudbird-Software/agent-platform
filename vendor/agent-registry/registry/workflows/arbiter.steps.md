# arbiter 固定流程（workflow.steps_ref——ADR-0022 补齐 issue #31 B-7）

1. **受理核对**：核对管辖依据（授权域枚举——仅 owner 可改，CT-JDG-003）；越域 → 判"上升人类"附原因
2. **机械预筛**：flaky 类先移交 evidence-pack 重跑（N 次 M 失败 → 自动判 flaky 隔离+开卡）——机器能判的不受理
3. **证据固定**：摘录双方主张（结构化）；依据检索只读仓内固定 ref 材料 + evidence-pack artifact（禁读团队工作区/争议方原始对话）
4. **判决**：decision + rationale + jurisdiction_basis + reversible_by=owner；对机制检索的候选判例逐项 follow/distinguish（禁自证"无相关判例"）
5. **落事件**：判决以 decision_made 自述事件经平台通道产出（无写凭据，CT-JDG-001）；判例入库执行者=curator
6. **销毁**：per_dispute 判后销毁（无跨实例私有判例记忆——判例唯一入口=case_law/ 只读）
