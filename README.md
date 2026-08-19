# agent-platform

agent-registry 声明 → **openjiuwen / jiuwenswarm** 运行时的渲染与治理执行层（L2，ADR-0025）。

```
L1  agent-registry（声明）               L2  agent-platform（本仓）               L3  运行态
┌──────────────────────────┐   渲染   ┌────────────────────────────────┐      ┌──────────────┐
│ registry/*.yaml          │ ───────▶ │ spec/ 加载 → render/ 渲染      │ ───▶ │ openjiuwen   │
│ standards/*.yaml         │          │ flow/  SwarmFlow 编译器        │      │ SwarmFlow    │
│ （vendor 快照随仓分发）   │ ◀─────── │ governance/ 机制执行面         │      │ + LLM Gateway│
│                          │  漂移门禁 │ drift/ 一致性/漂移  observe/ TUI│      └──────────────┘
└──────────────────────────┘          └────────────────────────────────┘
```

## 开箱即用（本机三步）

```bash
git clone https://github.com/Cloudbird-Software/agent-platform && cd agent-platform
make setup && make init            # vendor 声明快照 → workspace（渲染+state+env 模板）
cp workspace/.env.example workspace/.env   # 填 LLM_GATEWAY_ENDPOINT / LLM_GATEWAY_KEY
make doctor                        # 全绿即可用；执行流先跑零调用预检：
uv run ap up --workspace workspace --team dev-wave --dry-run
```

live 执行（真实 LLM 调用，治理挂点全接：预算闸/事件账本/模型解析）：

```bash
make setup-runtime                 # 安装 runtime/requirements.lock 钉版上游（一次性）
uv run ap up --workspace workspace --team dev-wave
```

观测与干预：

```bash
uv run ap tui --state workspace/state     # TUI：卡/预算/锁/事件 实时视图
agentctl --state workspace/state help     # 干预命令面（JSON 输出，供其他 agent 调用）
```

## 开箱即用（Docker）

```bash
cp .env.example .env               # 填 LLM_GATEWAY_KEY（+ provider 上游）
docker compose up -d               # gateway(LiteLLM) + platform(渲染+漂移守护)
docker compose run --rm platform up --workspace /app/workspace --team dev-wave --dry-run
```

声明源默认用仓内 `vendor/agent-registry` 快照（`PROVENANCE.yaml` 记源 commit+digest），
零参数 `init`；要指向自己的 registry checkout：`--registry <path>` 或 `AGENTPLATFORM_REGISTRY`。

## 上游钉版（ADR-0025）

- `openjiuwen==0.1.16`（PyPI wheel，含 SwarmFlow 引擎）
- `jiuwenswarm==0.2.3`（PyPI wheel，含预构建前端 dist）
- 零 fork / 零 submodule；定制只走 extensions / AgentBackend / rails / MCP / config

## 双锁面

| 锁面 | 文件 | 内容 | 审计策略 |
|---|---|---|---|
| 核心 | `uv.lock` | 本仓代码 + dev 依赖 | 零容忍（dep-review + pip-audit） |
| 运行时 | `runtime/requirements.lock` | openjiuwen/jiuwenswarm 钉版全树（含哈希） | 显式风险准入（见 `.github/workflows/ci.yml` 的准入清单） |

上游树含未发修复版的高危项（chromadb 预认证注入等），故与核心锁面分离：
不进 dependency-graph、不染红无关 PR；新出现的未登记漏洞仍然 fail-closed。
升级上游 = 改 `runtime/requirements.in` → `make runtime-lock` → 审计准入清单。

## 开发

```bash
make setup          # uv sync（含 dev）
make setup-runtime  # 可选：本机装上游运行时（Docker 部署不需要）
make check          # lint + arch（依赖边界）+ test
```

核心层（spec/render/flow/governance/drift/observe）零上游依赖——上游 import 只允许出现在
`adapter/` 与 `bootstrap/`（`scripts/arch_check.py` 强制）。

## 命令

- `ap` —— 渲染/一致性/启动控制台（`ap --help` 看全部：spec/render/flow/drift/init/doctor/envfile/up/tui/ctl）
- `agentctl` —— 内部运作干预命令（可被其他 agent 直接调用；`agentctl help` 列出全部动词）
