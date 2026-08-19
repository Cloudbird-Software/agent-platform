# Changelog

本文件记录对外可见的变更。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### Added
- 初始模板工程（CI gate / hygiene / dependabot / automerge 全套护栏）。
- 声明加载器（SpecSnapshot 指纹化快照/引用完整性/泄漏扫描）。
- SwarmFlow 编译器（相位图 IR + 确定性 codegen + dry-run lint 含模块级执行校验）。
- 治理执行面（事件哈希链账本/卡门/三层预算/写锁）。
- 三方对账漂移检测（spec/file/orphan + watch 事件流）。
- TUI 可观测性 + agentctl 21 动词干预命令面（JSON 输出）。
- adapter 治理挂点（agent_gate/observer/model_resolver + 工具轨道）。
- 开箱即用：vendor 快照 + init/doctor/envfile/up + docker compose。

### Fixed
- live 执行：agent() 调用补 options.model（座位声明 alias；verdict worker 按
  use_for=arbitration 归因）——此前无 hint 调用 resolver(None) 全座位崩。
- live 执行：run_swarmflow 补 worker_base_spec（default 网关配置基座）——
  此前上游 backend 无基座拒建 worker harness。
- drift：__pycache__/*.pyc 运行时字节码缓存不再误报 orphan。
- 渲染：schema 常量双重花括号 set 字面量；观测桥非标量事件体收敛；
  budget.remaining() 无界返回 None 判空。
