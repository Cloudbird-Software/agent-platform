"""渲染 manifest——输出面的指纹账本（漂移检测的对账基准）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agentplatform.spec.fingerprint import content_digest, sha256_hex

MANIFEST_SCHEMA = 1
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class RenderManifest:
    spec_digest: str
    renderer_version: str
    files: dict[str, str]  # 相对路径 → sha256（manifest 自身除外）
    env_refs: tuple[str, ...]
    notes: dict = field(default_factory=dict)

    @property
    def digest(self) -> str:
        """输出面身份：文件账本的规范化摘要。"""
        return content_digest(
            {"spec": self.spec_digest, "version": self.renderer_version, "files": self.files}
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "schema": MANIFEST_SCHEMA,
                "spec_digest": self.spec_digest,
                "renderer_version": self.renderer_version,
                "output_digest": self.digest,
                "env_refs": list(self.env_refs),
                "notes": self.notes,
                "files": self.files,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

    def write(self, out_dir: Path) -> Path:
        path = out_dir / MANIFEST_NAME
        path.write_text(self.to_json() + "\n", encoding="utf-8")
        return path


def load_manifest(out_dir: Path) -> RenderManifest:
    path = out_dir / MANIFEST_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"manifest schema {data.get('schema')} != {MANIFEST_SCHEMA}")
    m = RenderManifest(
        spec_digest=data["spec_digest"],
        renderer_version=data["renderer_version"],
        files={k: v for k, v in data["files"].items()},
        env_refs=tuple(data.get("env_refs", [])),
        notes=data.get("notes", {}),
    )
    if data.get("output_digest") != m.digest:
        raise ValueError("manifest 自校验失败：output_digest 与文件账本不一致（账本被篡改？）")
    return m


def hash_file_bytes(path: Path) -> str:
    return sha256_hex(path.read_text(encoding="utf-8"))
