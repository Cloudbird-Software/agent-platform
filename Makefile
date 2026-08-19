.PHONY: setup setup-runtime runtime-lock fmt lint arch test build check init doctor up vendor vendor-check vendor-update drill docker-build

setup:  ; uv sync --all-extras
# 运行时锁面（ADR-0025）：openjiuwen/jiuwenswarm 钉版 + 哈希锁，
# 与核心 uv.lock 分离（上游树含未修高危，独立审计面）。
setup-runtime:  ; uv pip install -r runtime/requirements.lock
runtime-lock:   ; uv pip compile runtime/requirements.in --generate-hashes -o runtime/requirements.lock
fmt:    ; uv run ruff format src tests scripts && uv run ruff check --fix src tests scripts
lint:   ; uv run ruff format --check src tests scripts && uv run ruff check src tests scripts
# arch = 依赖边界（ADR-0025）：核心层（spec/render/flow/governance/drift/observe）
# 禁 import openjiuwen/jiuwenswarm——上游只允许出现在 adapter/ 与 bootstrap。
arch:   ; uv run python scripts/arch_check.py
test:   ; uv run pytest --cov --cov-report=term-missing
build:  ; uv build
check:  lint arch test

# ── 开箱即用三步（ADR-0025 bootstrap）──────────────────────────────
# make init → cp workspace/.env.example workspace/.env 填凭据 → make doctor
# 默认声明源 = vendor/agent-registry 快照（零参数；AGENTPLATFORM_REGISTRY 可覆盖）
init:   ; uv run ap init --out workspace
doctor: ; uv run ap doctor --registry vendor/agent-registry --workspace workspace
up:     ; uv run ap up --workspace workspace --team $(T)

# vendor 快照：check 只读对账（CI 门禁）；update 需显式传 agent-registry 路径
vendor-check:   ; uv run python scripts/vendor_registry.py
vendor-update:  ; uv run python scripts/vendor_registry.py $(REG) --update

# 终验演练：init→填 env→doctor→dry-run→ctl 动词→对抗注入→恢复（全链路）
drill:  ; uv run python scripts/drill.py

docker-build:   ; docker build -t agent-platform:latest .
