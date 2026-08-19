.PHONY: setup setup-runtime runtime-lock fmt lint arch test build check

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
