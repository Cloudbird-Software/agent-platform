"""对抗性测试：渲染一致性攻击面。

覆盖矩阵：
  B1 输出面篡改（手改 config.yaml）→ manifest 对账必须失败
  B2 manifest 账本篡改（删条目让漂移静默）
  B3 manifest output_digest 篡改（改 files 后不重算总账）
  B4 声明变更 → spec_digest 变 → manifest 指纹变（防重放旧输出）
  B5 输出目录异物（多余文件混入）→ check 必须报
  B6 时间不稳定攻击：渲染输出不得含时间戳/随机内容（两次渲染字节一致已由
     幂等测试覆盖，这里验证 manifest 无时间字段）
  B7 密钥泄漏面：渲染输出不得含明文密钥（env 符号透传验证）
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from agentplatform.render import Renderer, load_manifest
from agentplatform.spec import RegistryLoader
from agentplatform.spec.fingerprint import sha256_hex

FIXTURE = Path(__file__).parent / "fixtures" / "mini-registry"
pytestmark = pytest.mark.adversarial


@pytest.fixture()
def ws(tmp_path: Path):
    reg = tmp_path / "reg"
    shutil.copytree(FIXTURE, reg)
    out = tmp_path / "ws"
    snap = RegistryLoader().load(reg)
    manifest = Renderer().render(snap, out)
    return reg, out, snap, manifest


def _recheck(out: Path) -> list[str]:
    """drift 逻辑的本地等价（PR-6 会做成模块，这里直接对账）。"""
    m = load_manifest(out)
    problems = []
    for rel, want in m.files.items():
        p = out / rel
        if not p.is_file():
            problems.append(f"missing:{rel}")
        elif sha256_hex(p.read_text(encoding="utf-8")) != want:
            problems.append(f"tampered:{rel}")
    actual = {p.name for p in out.iterdir() if p.is_file() and p.name != "manifest.json"}
    extra = actual - set(m.files)
    problems.extend(f"extra:{e}" for e in sorted(extra))
    return problems


def test_b1_output_tamper_detected(ws) -> None:
    _reg, out, _snap, _manifest = ws
    cfg = out / "config.yaml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace("permission_mode: normal", "permission_mode: lenient"),
        encoding="utf-8",
    )
    assert any(p.startswith("tampered:") for p in _recheck(out))


def test_b2_manifest_ledger_tamper(ws) -> None:
    _reg, out, _snap, _manifest = ws
    data = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    cfg_rel = "config.yaml"
    # 攻击：把账本里的哈希改成篡改后文件的哈希（让对账通过）——但 output_digest
    # 是账本的摘要，改账本必然使总账失配 → load_manifest 拒绝
    data["files"][cfg_rel] = sha256_hex("attacker controlled")
    (out / "manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="自校验失败"):
        load_manifest(out)


def test_b3_full_manifest_forgery_fails(ws) -> None:
    reg, out, _snap, _manifest = ws
    data = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    data["files"]["config.yaml"] = sha256_hex("attacker controlled")
    # 攻击者连 output_digest 一起重算（绕过 B2）——此时账本内部一致，
    # 但 spec_digest 仍是旧声明；重新渲染会得到不同 digest → 检测移交
    # 到"spec vs manifest"对账（drift 层）：这里验证重渲染 digest 不同
    from agentplatform.render.manifest import RenderManifest

    forged = RenderManifest(
        spec_digest=data["spec_digest"],
        renderer_version=data["renderer_version"],
        files=data["files"],
        env_refs=tuple(data["env_refs"]),
        notes=data.get("notes", {}),
    )
    data["output_digest"] = forged.digest
    (out / "manifest.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    m2 = load_manifest(out)  # 账本内部一致，可加载
    fresh = Renderer().render(RegistryLoader().load(reg), out.parent / "fresh")
    assert m2.digest != fresh.digest  # 与真渲染对不上


def test_b4_spec_change_changes_manifest(ws) -> None:
    reg, out, snap, manifest = ws
    p = reg / "registry" / "agents" / "mini-builder.yaml"
    p.write_text(p.read_text(encoding="utf-8") + "\nrole: 迷你夹具：builder v2\n", encoding="utf-8")
    snap2 = RegistryLoader().load(reg)
    assert snap2.digest != snap.digest
    fresh = Renderer().render(snap2, out.parent / "fresh2")
    assert fresh.spec_digest != manifest.spec_digest


def test_b5_extra_files_detected(ws) -> None:
    _reg, out, _snap, _manifest = ws
    (out / "rogue.yaml").write_text("pwned: true\n", encoding="utf-8")
    assert "extra:rogue.yaml" in _recheck(out)


def test_b6_manifest_has_no_timestamps(ws) -> None:
    text = (ws[1] / "manifest.json").read_text(encoding="utf-8")
    assert "time" not in text.lower().replace("runtime", "")


def test_b7_no_plaintext_secrets_in_output(ws) -> None:
    text = (ws[1] / "config.yaml").read_text(encoding="utf-8")
    for marker in ("ghp_", "sk-", "AKIA", "PRIVATE KEY", "postgres://"):
        assert marker not in text, marker
    assert "${LLM_GATEWAY_KEY}" in text  # 密钥以 env 符号存在
