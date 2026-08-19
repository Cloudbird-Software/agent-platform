"""bootstrap/ 测试：init 幂等 / envfile / doctor 各面 / up 预检 / vendor 快照。

全部零上游依赖（runtime 面只出现 warn，不 fail）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentplatform.bootstrap import default_registry, init_workspace, render_envfile, run_doctor
from agentplatform.bootstrap.paths import VENDOR_DIR
from agentplatform.bootstrap.up import UpError, run_up

FIXTURE = Path(__file__).parent / "fixtures" / "mini-registry"


# ── init ──────────────────────────────────────────────────────────────


class TestInit:
    def test_init_creates_workspace_state_envfile(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        summary = init_workspace(FIXTURE, ws)
        assert (ws / "manifest.json").is_file()
        assert (ws / "config.yaml").is_file()
        assert (ws / "models.json").is_file()
        assert (ws / ".env.example").is_file()
        assert (ws / "state" / "ledger.jsonl").is_file()
        assert summary["state_created"] is True
        assert summary["env_refs"], "mini-registry 渲染应引用 env"

    def test_init_idempotent_state_survives(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        init_workspace(FIXTURE, ws)
        ledger = ws / "state" / "ledger.jsonl"
        before = ledger.read_text(encoding="utf-8")
        summary = init_workspace(FIXTURE, ws)  # 二次 init
        assert summary["state_created"] is False
        assert ledger.read_text(encoding="utf-8") == before, "账本 append-only——init 绝不清洗"

    def test_init_default_registry_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTPLATFORM_REGISTRY", str(FIXTURE))
        summary = init_workspace(None, tmp_path / "ws")
        assert summary["files"] > 0

    def test_default_registry_bad_env_fails_loud(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENTPLATFORM_REGISTRY", "/nonexistent/xx")
        with pytest.raises(FileNotFoundError, match="AGENTPLATFORM_REGISTRY"):
            default_registry()

    def test_default_registry_vendor_snapshot(self) -> None:
        """仓内 vendor 快照可被零参发现（源码 checkout 场景）。"""
        reg = default_registry()
        assert reg is not None and (reg / "registry").is_dir()
        assert reg.name == "agent-registry"


# ── envfile ───────────────────────────────────────────────────────────


class TestEnvfile:
    def test_envfile_lists_env_refs_only(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        init_workspace(FIXTURE, ws)
        text = render_envfile(ws)
        from agentplatform.render.manifest import load_manifest

        for var in load_manifest(ws).env_refs:
            assert f"{var}=" in text
        assert "sk-" not in text and "password" not in text.lower(), "凭据永不带值"

    def test_envfile_prefills_local_defaults_not_credentials(self, tmp_path: Path) -> None:
        """开箱语义：凭据留空必填；非敏感基础设施变量预填本地默认。"""
        from agentplatform.bootstrap.envfile import default_for

        ws = tmp_path / "ws"
        init_workspace(FIXTURE, ws)
        text = render_envfile(ws)
        assert "LLM_GATEWAY_KEY=\n" in text and "LLM_GATEWAY_ENDPOINT=\n" in text
        assert "TEAM_WORKSPACE_ROOT=data/team-ws\n" in text
        assert "TEAM_DB_DSN=data/team.db\n" in text
        assert default_for("LLM_GATEWAY_KEY") is None
        assert default_for("SOME_UNKNOWN_TOKEN") is None, "未知变量不猜默认（fail-closed）"

    def test_out_of_box_doctor_green_with_only_gateway_filled(self, tmp_path: Path) -> None:
        """终验核心路径：cp 模板 → 只填网关两项 → doctor 全绿（ADR-0025 开箱承诺）。"""
        import shutil

        ws = tmp_path / "ws"
        init_workspace(FIXTURE, ws)
        shutil.copy(ws / ".env.example", ws / ".env")
        env = (ws / ".env").read_text(encoding="utf-8")
        (ws / ".env").write_text(
            env.replace("LLM_GATEWAY_ENDPOINT=", "LLM_GATEWAY_ENDPOINT=http://gw.invalid:4000").replace(
                "LLM_GATEWAY_KEY=", "LLM_GATEWAY_KEY=fake"
            ),
            encoding="utf-8",
        )
        result = run_doctor(registry=FIXTURE, workspace=ws, human=False)
        by = {c["name"]: c["status"] for c in result["checks"]}
        assert by["env"] == "pass", f"只填网关两项即应全绿：{result['checks']}"
        assert result["ok"] is True


# ── doctor ────────────────────────────────────────────────────────────


class TestDoctor:
    def test_all_pass_when_env_filled(self, tmp_path: Path) -> None:
        from agentplatform.render.manifest import load_manifest

        ws = tmp_path / "ws"
        init_workspace(FIXTURE, ws)
        (ws / ".env").write_text("\n".join(f"{v}=x" for v in load_manifest(ws).env_refs), encoding="utf-8")
        result = run_doctor(registry=FIXTURE, workspace=ws, human=False)
        by = {c["name"]: c["status"] for c in result["checks"]}
        assert by["python"] == by["core"] == by["workspace"] == by["env"] == "pass"
        assert by["drift"] == by["state"] == "pass"
        assert result["ok"] is True

    def test_env_fail_when_missing(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        init_workspace(FIXTURE, ws)
        result = run_doctor(registry=FIXTURE, workspace=ws, human=False)
        by = {c["name"]: c["status"] for c in result["checks"]}
        assert by["env"] == "fail" and result["ok"] is False

    def test_drift_fail_when_workspace_tampered(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        init_workspace(FIXTURE, ws)
        cfg = ws / "config.yaml"
        cfg.write_text(cfg.read_text(encoding="utf-8") + "# tampered\n", encoding="utf-8")
        result = run_doctor(registry=FIXTURE, workspace=ws, human=False)
        by = {c["name"]: c["status"] for c in result["checks"]}
        assert by["drift"] == "fail"

    def test_no_workspace_warns_not_fails(self) -> None:
        result = run_doctor(human=False)
        assert result["ok"] is True, "warn 不阻断（可渐进配置）"
        assert all(c["status"] != "fail" for c in result["checks"])


# ── up ────────────────────────────────────────────────────────────────


class TestUp:
    def test_dry_run_ok_with_env(self, tmp_path: Path) -> None:
        from agentplatform.render.manifest import load_manifest

        ws = tmp_path / "ws"
        init_workspace(FIXTURE, ws)
        (ws / ".env").write_text("\n".join(f"{v}=x" for v in load_manifest(ws).env_refs), encoding="utf-8")
        team = next(p.stem for p in (ws / "swarmflow").glob("*.py"))
        report = run_up(ws, team, dry_run=True)
        assert report["ok"] is True and report["mode"] == "dry-run"
        assert report["models_missing"] == []

    def test_dry_run_reports_missing_creds(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        init_workspace(FIXTURE, ws)
        team = next(p.stem for p in (ws / "swarmflow").glob("*.py"))
        report = run_up(ws, team, dry_run=True)
        if report["models_missing"]:
            assert report["ok"] is False

    def test_unknown_team_raises(self, tmp_path: Path) -> None:
        ws = tmp_path / "ws"
        init_workspace(FIXTURE, ws)
        with pytest.raises(UpError, match="不存在"):
            run_up(ws, "no-such-team", dry_run=True)

    def test_live_without_state_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from agentplatform.render.manifest import load_manifest

        ws = tmp_path / "ws"
        init_workspace(FIXTURE, ws)
        (ws / ".env").write_text("\n".join(f"{v}=x" for v in load_manifest(ws).env_refs), encoding="utf-8")
        import shutil

        shutil.rmtree(ws / "state")
        team = next(p.stem for p in (ws / "swarmflow").glob("*.py"))
        with pytest.raises(UpError, match="state 未初始化"):
            run_up(ws, team, dry_run=False)


# ── vendor 快照 ───────────────────────────────────────────────────────


class TestVendorSnapshot:
    def test_vendor_digest_matches_provenance(self) -> None:
        import subprocess
        import sys

        repo = Path(__file__).resolve().parent.parent
        r = subprocess.run(
            [sys.executable, str(repo / "scripts" / "vendor_registry.py")],
            capture_output=True,
            text=True,
            cwd=repo,
        )
        assert r.returncode == 0, r.stderr
        assert "快照一致" in r.stdout

    def test_vendor_dir_exists(self) -> None:
        repo = Path(__file__).resolve().parent.parent
        assert (repo / VENDOR_DIR / "registry").is_dir()
