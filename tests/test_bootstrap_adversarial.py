"""bootstrap/ 对抗性测试：路径穿越 / 凭据泄漏 / 篡改各面 / fail-closed。

威胁模型（ADR-0025 红队面）：
  T1 team id 路径穿越（../ 或绝对路径 → 执行任意脚本）
  T2 凭据泄漏（.env 值 / api_key 出现在任何 JSON 输出）
  T3 渲染产物篡改（models.json / config.yaml / swarmflow 脚本被改后仍被执行）
  T4 vendor 快照篡改（绕过 registry PR 流程直接改声明）
  T5 init 清洗用户数据（state 账本 / 已填 .env 被重渲染毁掉）
  T6 fail-closed 缺口（凭据缺失时 live 执行被放行）
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agentplatform.bootstrap import init_workspace, render_envfile, run_doctor, run_up
from agentplatform.bootstrap.up import UpError
from agentplatform.render.manifest import load_manifest

FIXTURE = Path(__file__).parent / "fixtures" / "mini-registry"
REPO = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.adversarial


def _init(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    init_workspace(FIXTURE, ws)
    return ws


def _fill_env(ws: Path, *, env_value: str = "x") -> None:
    (ws / ".env").write_text(
        "\n".join(f"{v}={env_value}" for v in load_manifest(ws).env_refs), encoding="utf-8"
    )


# 泄漏金丝雀运行时拼接——字面量形态会命中 gitleaks generic-api-key（secret 关键字+高熵）
_CANARY_UP = "sk-" + "LEAK-" + "CANARY-" + "9f1e"
_CANARY_DOCTOR = "sk-" + "DOCTOR-" + "LEAK"


def _first_team(ws: Path) -> str:
    return next(p.stem for p in (ws / "swarmflow").glob("*.py"))


# ── T1 路径穿越 ───────────────────────────────────────────────────────


class TestPathTraversal:
    @pytest.mark.parametrize("team", ["../evil", "../../etc/passwd", "/abs/evil", ".hidden", "a/../b", ".."])
    def test_team_id_traversal_rejected(self, tmp_path: Path, team: str) -> None:
        ws = _init(tmp_path)
        with pytest.raises(UpError, match="非法 team"):
            run_up(ws, team, dry_run=True)

    def test_traversal_rejected_before_any_disk_access(self, tmp_path: Path) -> None:
        """穿越拒绝先于一切磁盘读取——workspace 不存在也先报非法 id。"""
        with pytest.raises(UpError, match="非法 team"):
            run_up(tmp_path / "nope", "../evil", dry_run=True)

    def test_benign_team_id_accepted(self, tmp_path: Path) -> None:
        ws = _init(tmp_path)
        _fill_env(ws)
        report = run_up(ws, _first_team(ws), dry_run=True)
        assert "非法" not in json.dumps(report)


# ── T2 凭据泄漏 ───────────────────────────────────────────────────────


class TestCredentialLeak:
    def test_env_values_never_in_output(self, tmp_path: Path) -> None:
        ws = _init(tmp_path)
        _fill_env(ws, env_value=_CANARY_UP)
        out = json.dumps(run_up(ws, _first_team(ws), dry_run=True), default=str)
        assert "sk-LEAK" not in out

    def test_envfile_template_has_no_values(self, tmp_path: Path) -> None:
        ws = _init(tmp_path)
        (ws / ".env").write_text("LLM_GATEWAY_KEY=sk-real-1\n", encoding="utf-8")
        assert "sk-real-1" not in render_envfile(ws)

    def test_doctor_output_has_no_values(self, tmp_path: Path) -> None:
        ws = _init(tmp_path)
        _fill_env(ws, env_value=_CANARY_DOCTOR)
        result = run_doctor(registry=FIXTURE, workspace=ws, human=False)
        assert "sk-DOCTOR-LEAK" not in json.dumps(result)


# ── T3 渲染产物篡改 ──────────────────────────────────────────────────


class TestTamperWorkspace:
    def test_tampered_models_json_blocks_dryrun(self, tmp_path: Path) -> None:
        ws = _init(tmp_path)
        _fill_env(ws)
        mj = ws / "models.json"
        data = json.loads(mj.read_text(encoding="utf-8"))
        data["gateway"]["api_key"] = "env:ATTACKER_KEY"  # 凭据重指向攻击者变量
        mj.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(UpError, match="篡改"):
            run_up(ws, _first_team(ws), dry_run=True)

    def test_tampered_flow_script_blocks(self, tmp_path: Path) -> None:
        ws = _init(tmp_path)
        _fill_env(ws)
        script = next((ws / "swarmflow").glob("*.py"))
        script.write_text(
            script.read_text(encoding="utf-8") + "\nimport os; os.system('evil')\n", encoding="utf-8"
        )
        with pytest.raises(UpError, match="篡改"):
            run_up(ws, script.stem, dry_run=True)

    def test_deleted_rendered_file_blocks(self, tmp_path: Path) -> None:
        ws = _init(tmp_path)
        (ws / "models.json").unlink()
        with pytest.raises(UpError, match="缺失"):
            run_up(ws, _first_team(ws), dry_run=True)

    def test_drift_surface_also_flags_tamper(self, tmp_path: Path) -> None:
        """双保险：执行面挡住，doctor/drift 面也要能诊断。"""
        ws = _init(tmp_path)
        cfg = ws / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8") + "# tampered\n", encoding="utf-8")
        report = run_doctor(registry=FIXTURE, workspace=ws, human=False)
        by = {c["name"]: c["status"] for c in report["checks"]}
        assert by["drift"] == "fail"


# ── T4 vendor 快照篡改 ───────────────────────────────────────────────


class TestVendorTamper:
    def test_tampered_snapshot_changes_digest(self) -> None:
        from agentplatform.spec import RegistryLoader

        vendor = REPO / "vendor" / "agent-registry"
        import yaml

        recorded = yaml.safe_load((vendor / "PROVENANCE.yaml").read_text(encoding="utf-8")).get("spec_digest")
        assert RegistryLoader().load(vendor).digest == recorded, "干净快照一致（前置）"

        victim = next((vendor / "registry" / "agents").glob("*.yaml"))
        orig = victim.read_text(encoding="utf-8")
        try:
            # 纯注释不改变语义 digest（记录该性质——指纹是语义级非字节级）
            victim.write_text(orig + "\n# comment only\n", encoding="utf-8")
            assert RegistryLoader().load(vendor).digest == recorded, "注释不影响语义"
            # 语义篡改（新增键）必须改变 digest
            victim.write_text(orig + "\nx-tamper: injected\n", encoding="utf-8")
            assert RegistryLoader().load(vendor).digest != recorded, "语义篡改必须改变 digest"
        finally:
            victim.write_text(orig, encoding="utf-8")
        assert RegistryLoader().load(vendor).digest == recorded, "复原后一致"

    def test_vendor_check_script_exit_code(self) -> None:
        r = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "vendor_registry.py")],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert r.returncode == 0, r.stderr


# ── T5 init 清洗用户数据 ─────────────────────────────────────────────


class TestInitPreservesUserData:
    def test_reinit_preserves_env_and_ledger(self, tmp_path: Path) -> None:
        ws = _init(tmp_path)
        (ws / ".env").write_text("LLM_GATEWAY_KEY=sk-keep", encoding="utf-8")
        ledger = ws / "state" / "ledger.jsonl"
        before = ledger.read_text(encoding="utf-8")
        init_workspace(FIXTURE, ws)  # 重渲染
        assert (ws / ".env").read_text(encoding="utf-8") == "LLM_GATEWAY_KEY=sk-keep"
        assert ledger.read_text(encoding="utf-8") == before


# ── T6 fail-closed ───────────────────────────────────────────────────


class TestFailClosed:
    def test_live_blocked_when_creds_missing(self, tmp_path: Path) -> None:
        ws = _init(tmp_path)
        with pytest.raises(UpError, match="凭据缺失"):
            run_up(ws, _first_team(ws), dry_run=False)

    def test_partial_creds_blocked(self, tmp_path: Path) -> None:
        ws = _init(tmp_path)
        (ws / ".env").write_text("LLM_GATEWAY_ENDPOINT=x\n", encoding="utf-8")
        with pytest.raises(UpError, match="凭据缺失"):
            run_up(ws, _first_team(ws), dry_run=False)

    def test_dryrun_ok_false_when_missing(self, tmp_path: Path) -> None:
        ws = _init(tmp_path)
        assert run_up(ws, _first_team(ws), dry_run=True)["ok"] is False
