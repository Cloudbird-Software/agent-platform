.PHONY: setup fmt lint arch test build check

setup:  ; uv sync --all-extras
fmt:    ; uv run ruff format src tests scripts && uv run ruff check --fix src tests scripts
lint:   ; uv run ruff format --check src tests scripts && uv run ruff check src tests scripts
# arch = 依赖边界（ADR-0025）：核心层（spec/render/flow/governance/drift/observe）
# 禁 import openjiuwen/jiuwenswarm——上游只允许出现在 adapter/ 与 bootstrap。
arch:   ; uv run python scripts/arch_check.py
test:   ; uv run pytest --cov --cov-report=term-missing
build:  ; uv build
check:  lint arch test
