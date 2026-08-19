# agent-platform

agent-registry 声明 → **openjiuwen / jiuwenswarm** 运行时的渲染与治理执行层（L2，ADR-0025）。

```
L1  agent-registry（声明，不动）          L2  agent-platform（本仓）               L3  运行态
┌──────────────────────────┐   渲染   ┌────────────────────────────────┐      ┌──────────────┐
│ registry/*.yaml          │ ───────▶ │ spec/ 加载 → render/ 渲染      │ ───▶ │ jiuwenswarm  │
│ standards/*.yaml         │          │ flow/  SwarmFlow 编译器        │      │ workspace +  │
│ schemas/*.json           │ ◀─────── │ governance/ 机制执行面         │      │ AgentServer  │
│ (validate + simulate)    │  漂移门禁 │ drift/ 一致性/漂移  observe/ TUI│      │ + Gateway    │
└──────────────────────────┘          └────────────────────────────────┘      └──────────────┘
```

## 开箱即用（三条命令）

```bash
git clone https://github.com/Cloudbird-Software/agent-platform && cd agent-platform
cp .env.example .env          # 填 LLM_GATEWAY_ENDPOINT / LLM_GATEWAY_KEY
docker compose up -d          # 拉起：LiteLLM 网关 + 渲染 + jiuwenswarm（AgentServer+Gateway+Web）
```

无 Docker：`make setup && ap render --registry <agent-registry 路径> --out ~/.agentplatform && ap up`。

## 上游钉版（ADR-0025）

- `openjiuwen==0.1.16.post6`（PyPI wheel，含 SwarmFlow 引擎）
- `jiuwenswarm==0.2.3`（PyPI wheel，含预构建前端 dist）
- 零 fork / 零 submodule；定制只走 extensions / AgentBackend / rails / MCP / config

## 开发

```bash
make setup    # uv sync（含 dev）
make check    # lint + arch（依赖边界）+ test
```

核心层（spec/render/flow/governance/drift/observe）零上游依赖——上游 import 只允许出现在
`adapter/` 与 `bootstrap/`（`scripts/arch_check.py` 强制）。

## 命令

- `ap` —— 渲染/一致性/启动控制台（随 PR 逐步就位）
- `agentctl` —— 内部运作干预命令（可被其他 agent 直接调用）
