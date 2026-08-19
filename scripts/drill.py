#!/usr/bin/env python3
"""drill：开箱即用终验演练——模拟用户从零到可用的完整路径。

场景（等价于 README 快速开始 + 一次对抗注入）：
  1. fresh init（vendor 快照 → workspace）
  2. 填 .env（演练用假凭据）
  3. doctor 全绿（runtime 面允许 warn）
  4. flow dry-run：全部 alias + 凭据解析通过（零 LLM 调用）
  5. agentctl：观测/干预动词往返（status/card-ratify/budget/verify）
  6. 对抗注入：篡改渲染产物 → dry-run 必须 fail-closed
  7. 重渲染恢复 → dry-run 复绿

用法：uv run python scripts/drill.py [--keep]
退出码 0 = 演练全过；非 0 = 某步失败（步骤名打到 stderr）。
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "vendor" / "agent-registry"
STEPS: list[str] = []


def step(name: str) -> None:
    STEPS.append(name)
    print(f"[drill] ✓ {name}", flush=True)


def fail(name: str, detail: str) -> None:
    print(f"[drill] ✗ {name}: {detail}", file=sys.stderr, flush=True)
    sys.exit(1)


def run(*cmd: str, expect: int = 0) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    if r.returncode != expect:
        fail(cmd[0], f"exit={r.returncode} expect={expect}\n{r.stdout[-800:]}\n{r.stderr[-800:]}")
    return r


def ap(*args: str, expect: int = 0) -> subprocess.CompletedProcess:
    return run("uv", "run", "ap", *args, expect=expect)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="保留演练 workspace（调试用）")
    args = parser.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="ap-drill-"))
    ws = tmp / "workspace"
    try:
        # 1. fresh init
        summary = json.loads(ap("init", "--out", str(ws)).stdout)
        assert summary["flows"], "渲染产物必须含 flow 脚本"
        step(f"init（{summary['files']} 文件，digest {summary['spec_digest'][:8]}）")

        # 2. 填 .env（演练用假凭据——真实值由用户填）
        env_example = (ws / ".env.example").read_text(encoding="utf-8")
        (ws / ".env").write_text(
            "\n".join(
                line + "drill-fake"
                for line in env_example.splitlines()
                if line and not line.startswith("#") and line.endswith("=")
            ),
            encoding="utf-8",
        )
        step("填 .env（假凭据）")

        # 3. doctor 全绿（runtime 面允许 warn——零上游环境）
        doc = json.loads(ap("doctor", "--registry", str(REGISTRY), "--workspace", str(ws), "--json").stdout)
        fails = [c for c in doc["checks"] if c["status"] == "fail"]
        if fails or not doc["ok"]:
            fail("doctor", json.dumps(fails, ensure_ascii=False))
        step("doctor 全绿")

        # 4. 每个团队 dry-run
        for flow in summary["flows"]:
            team = flow.rsplit("/", 1)[-1][:-3]
            rep = json.loads(ap("up", "--workspace", str(ws), "--team", team, "--dry-run").stdout)
            if not rep.get("ok"):
                fail(f"dry-run {team}", json.dumps(rep.get("models_missing"), ensure_ascii=False))
        step(f"dry-run ×{len(summary['flows'])} 团队（凭据/脚本/完整性全过）")

        # 5. agentctl 动词往返
        state = str(ws / "state")
        verbs = [
            ("status",),
            ("cards",),
            ("budget",),
            ("verify",),
            ("events", "--tail", "5"),
            ("lock-acquire", "drill-artifact", "--seat", "drill-seat"),
            ("locks",),
            ("lock-release", "drill-artifact", "--seat", "drill-seat"),
            ("ledger-export",),
            ("flow-teams", str(ws)),
            ("flow-dryrun", str(ws), summary["flows"][0].rsplit("/", 1)[-1][:-3]),
            ("doctor", "--workspace", str(ws), "--registry", str(REGISTRY)),
        ]
        for v in verbs:
            r = run("uv", "run", "agentctl", "--state", state, *v)
            out = json.loads(r.stdout)
            if out.get("ok") is not True:
                fail(f"ctl {v[0]}", r.stdout[:400])
        step(f"agentctl ×{len(verbs)} 动词（观测/干预/执行面）")

        # 6. 对抗注入：篡改模型表 → 必须 fail-closed
        mj = ws / "models.json"
        data = json.loads(mj.read_text(encoding="utf-8"))
        data["gateway"]["api_key"] = "env:ATTACKER"
        mj.write_text(json.dumps(data), encoding="utf-8")
        team0 = summary["flows"][0].rsplit("/", 1)[-1][:-3]
        ap("up", "--workspace", str(ws), "--team", team0, "--dry-run", expect=1)
        step("对抗注入：篡改 models.json → dry-run fail-closed（exit 1）")

        # 7. 重渲染恢复 → 复绿（state/.env 保留）
        ledger_before = (ws / "state" / "ledger.jsonl").read_text(encoding="utf-8")
        ap("init", "--out", str(ws))
        if (ws / "state" / "ledger.jsonl").read_text(encoding="utf-8") != ledger_before:
            fail("re-render", "账本被清洗！")
        ap("up", "--workspace", str(ws), "--team", team0, "--dry-run")
        step("重渲染恢复 → dry-run 复绿（账本/.env 保留）")

        print(f"[drill] 演练完成：{len(STEPS)} 步全过")
        return 0
    finally:
        if args.keep:
            print(f"[drill] workspace 保留：{ws}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
