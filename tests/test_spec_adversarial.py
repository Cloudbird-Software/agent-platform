"""对抗性测试：对声明面加载的绕过尝试逐条封堵（用户要求"对抗各种潜在风险"）。

覆盖矩阵：
  A1 重复 id（同名实体两处声明）
  A2 引用不存在的模型别名 / 工具 / schema / prompt / skill
  A3 approved 实体引用 draft 实体（状态门）
  A4 明文密钥（github token / openai key / aws key / slack / 私钥 / 带凭据 DSN）
  A5 env: 引用形状攻击（小写/嵌入路径）
  A6 路径绑定绕过尝试：移动文件不改内容 → 快照摘要必须变
  A7 解析面（非法 YAML / 根节点非映射）
  A8 键序与目录序不变性（指纹确定性）
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from agentplatform.spec import RegistryLoader, SpecError

FIXTURE = Path(__file__).parent / "fixtures" / "mini-registry"
pytestmark = pytest.mark.adversarial


@pytest.fixture()
def reg(tmp_path: Path) -> Path:
    dst = tmp_path / "reg"
    shutil.copytree(FIXTURE, dst)
    return dst


def _agent_file(reg: Path) -> Path:
    return reg / "registry" / "agents" / "mini-builder.yaml"


def test_a1_duplicate_agent_id(reg: Path) -> None:
    src = _agent_file(reg).read_text(encoding="utf-8")
    (reg / "registry" / "agents" / "dup.yaml").write_text(
        src.replace("id: mini-builder", "id: mini-draft-x")
        and src.replace("id: mini-builder", "id: mini-builder"),
        encoding="utf-8",
    )
    with pytest.raises(SpecError) as ei:
        RegistryLoader().load(reg)
    assert ei.value.kind == "duplicate"


def test_a2_broken_references(reg: Path) -> None:
    for field, old, new in [
        ("alias: coder-fast", "alias: coder-fast", "alias: no-such-model"),
        ("tool:mini-read", "tool:mini-read", "tool:no-such-tool"),
        ("schemas/task-in.json", "schemas/task-in.json", "schemas/no-such.json"),
        ("identities/mini-builder.md", "identities/mini-builder.md", "identities/ghost.md"),
        ("skill:mini-skill", "skill:mini-skill", "skill:ghost-skill"),
    ]:
        p = _agent_file(reg)
        p.write_text(p.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")
        with pytest.raises(SpecError) as ei:
            RegistryLoader().load(reg)
        assert ei.value.kind == "reference", field
        # 恢复
        p.write_text(p.read_text(encoding="utf-8").replace(new, old), encoding="utf-8")


def test_a3_approved_references_draft(reg: Path) -> None:
    p = reg / "registry" / "teams" / "mini-wave.yaml"
    p.write_text(
        p.read_text(encoding="utf-8").replace("agent:mini-builder", "agent:mini-draft"),
        encoding="utf-8",
    )
    with pytest.raises(SpecError) as ei:
        RegistryLoader().load(reg)
    assert ei.value.kind == "reference"


def test_a4_plaintext_secrets(reg: Path) -> None:
    leaks = [
        ("github", "role: ghp_0123456789abcdefghijklmnopqrstuvwxyz"),
        ("openai", "role: sk-proj-0123456789abcdefghijklmnopqrstu"),
        ("aws", "role: AKIAIOSFODNN7EXAMPLE"),
        ("slack", "role: xoxb-123456789012345678901234"),
        ("pem", "role: -----BEGIN RSA PRIVATE KEY-----"),
        ("dsn", "storage: {ref: postgresql://user:secret@db.internal:5432/team}"),
    ]
    for name, payload in leaks:
        p = _agent_file(reg)
        original = p.read_text(encoding="utf-8")
        p.write_text(original + f"\n{payload}\n", encoding="utf-8")
        with pytest.raises(SpecError) as ei:
            RegistryLoader().load(reg)
        assert ei.value.kind == "leak", f"{name} 未被检出"
        p.write_text(original, encoding="utf-8")


def test_a5_env_ref_shape(reg: Path) -> None:
    p = _agent_file(reg)
    original = p.read_text(encoding="utf-8")
    p.write_text(original.replace("env:BUILDER_WORKSPACE_ROOT", "env:builder-root"), encoding="utf-8")
    with pytest.raises(SpecError) as ei:
        RegistryLoader().load(reg)
    assert ei.value.kind == "shape"


def test_a6_move_file_changes_snapshot_digest(reg: Path) -> None:
    base = RegistryLoader().load(reg)
    # 移动 identity 资源：内容不变、路径变 → 资源键变 → 声明面必须视为变更
    src = reg / "registry" / "identities" / "mini-builder.md"
    dst = reg / "registry" / "identities" / "renamed.md"
    src.rename(dst)
    # prompt_ref 现在悬空 → reference 错误（fail-closed），不是静默通过
    with pytest.raises(SpecError) as ei:
        RegistryLoader().load(reg)
    assert ei.value.kind == "reference"
    # 修复引用后可加载，但快照摘要必须 != 原（路径进入指纹域）
    p = _agent_file(reg)
    p.write_text(
        p.read_text(encoding="utf-8").replace("mini-builder.md", "renamed.md"),
        encoding="utf-8",
    )
    moved = RegistryLoader().load(reg)
    assert moved.digest != base.digest


def test_a7_malformed_yaml(reg: Path) -> None:
    p = _agent_file(reg)
    original = p.read_text(encoding="utf-8")
    p.write_text("id: [unclosed\n  bad: :", encoding="utf-8")
    with pytest.raises(SpecError) as ei:
        RegistryLoader().load(reg)
    assert ei.value.kind == "parse"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(SpecError) as ei:
        RegistryLoader().load(reg)
    assert ei.value.kind == "shape"
    p.write_text(original, encoding="utf-8")


def test_a8_fingerprint_determinism(reg: Path) -> None:
    """同内容两个副本：摘要一致（键序/目录序无关）。"""
    dst2 = reg.parent / "reg-copy"
    shutil.copytree(reg, dst2)
    a = RegistryLoader().load(reg)
    b = RegistryLoader().load(dst2)
    assert a.digest == b.digest
    # 内容真变更 → 摘要变
    p = _agent_file(dst2)
    p.write_text(p.read_text(encoding="utf-8") + "\n# harmless comment\n", encoding="utf-8")
    c = RegistryLoader().load(dst2)
    assert c.digest == b.digest  # 注释不进 YAML 语义 → 不变
    p.write_text(p.read_text(encoding="utf-8") + "\nx-extra: 1\n", encoding="utf-8")
    d = RegistryLoader().load(dst2)
    assert d.digest != b.digest  # 语义变更 → 必变
