#!/usr/bin/env python3
"""vendor：把 agent-registry 声明面快照固化进本仓（开箱即用的声明真源）。

用法：
    python scripts/vendor_registry.py <agent-registry checkout> [--update]

- 复制 registry/ + standards/（loader/flow 只读这两棵树）到 vendor/agent-registry/
- 写 PROVENANCE.yaml：源 commit、spec_digest、生成时间、工具版本
- 默认只读校验：重算 digest 与 PROVENANCE 不一致即退出非 0（防手改快照）；
  --update 才允许刷新快照与 PROVENANCE。

为什么 vendor 而不是 submodule：用户 clone 本仓即得完整声明面，无需二跳拉仓、
无需部署端凭据；漂移检测以 vendor 快照为基线（PR-10 CI 每日对上游 registry 对账）。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENDOR = REPO / "vendor" / "agent-registry"
SUBDIRS = ("registry", "standards")
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache")


def _source_commit(root: Path) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"vendor: 无法读取源 commit（{e}）——PROVENANCE 将记 unknown", file=sys.stderr)
        return "unknown"


def _copy(root: Path) -> None:
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    VENDOR.mkdir(parents=True)
    for sub in SUBDIRS:
        src = root / sub
        if not src.is_dir():
            print(f"vendor: 源缺 {sub}/（不是 agent-registry checkout？）", file=sys.stderr)
            sys.exit(2)
        shutil.copytree(src, VENDOR / sub, ignore=IGNORE)


def _digest() -> str:
    from agentplatform.spec import RegistryLoader

    return RegistryLoader().load(VENDOR).digest


def _read_provenance() -> dict[str, str]:
    p = VENDOR / "PROVENANCE.yaml"
    if not p.is_file():
        return {}
    import yaml

    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", nargs="?", help="agent-registry checkout 路径（--update 必填）")
    ap.add_argument("--update", action="store_true", help="刷新快照与 PROVENANCE（默认只读校验）")
    args = ap.parse_args()

    if args.update:
        if not args.source:
            ap.error("--update 需要 <agent-registry checkout> 路径")
        root = Path(args.source).resolve()
        _copy(root)
        digest = _digest()
        prov = {
            "source_repo": "Cloudbird-Software/agent-registry",
            "source_commit": _source_commit(root),
            "spec_digest": digest,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "tool": "scripts/vendor_registry.py",
        }
        import yaml

        (VENDOR / "PROVENANCE.yaml").write_text(
            yaml.safe_dump(prov, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        print(f"vendor: 快照已更新（commit {prov['source_commit'][:12]}，digest {digest[:16]}）")
        return 0

    # 只读校验：vendor 内容 digest 必须与 PROVENANCE 一致（防快照被手改）
    if not (VENDOR / "registry").is_dir():
        print("vendor: 快照不存在（先 --update 生成）", file=sys.stderr)
        return 2
    recorded = _read_provenance().get("spec_digest")
    actual = _digest()
    if recorded != actual:
        print(
            f"vendor: 快照漂移！PROVENANCE={str(recorded)[:16]} 实测={actual[:16]}"
            "（手改了 vendor？重新 --update）",
            file=sys.stderr,
        )
        return 1
    print(f"vendor: 快照一致（digest {actual[:16]}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
