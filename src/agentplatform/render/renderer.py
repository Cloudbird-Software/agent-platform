"""渲染器：SpecSnapshot → 磁盘 workspace（幂等、无时间戳、env 符号化）。"""

from __future__ import annotations

from pathlib import Path

import yaml

import agentplatform
from agentplatform.render.manifest import RenderManifest
from agentplatform.render.targets import build_config, collect_env_vars
from agentplatform.spec import SpecSnapshot

CONFIG_NAME = "config.yaml"


def _dump_yaml(doc: dict) -> str:
    return yaml.safe_dump(doc, sort_keys=True, allow_unicode=True, default_flow_style=False, width=100)


class Renderer:
    """纯函数渲染：同 (spec_digest, renderer_version) → 字节级相同输出。

    include_flows：连同 SwarmFlow 编译产物（swarmflow/<team>.py）一并写出
    并纳入 manifest 指纹账本——输出面的完整身份。
    """

    def render(
        self,
        snap: SpecSnapshot,
        out_dir: str | Path,
        *,
        clean: bool = True,
        include_flows: bool = True,
    ) -> RenderManifest:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        if clean:
            for p in sorted(out.rglob("*"), reverse=True):
                if p.is_file():
                    p.unlink()
            for p in sorted(out.rglob("*")):
                if p.is_dir():
                    p.rmdir()

        config, notes = build_config(snap, snap.root)
        config_yaml = _dump_yaml(config)
        (out / CONFIG_NAME).write_text(config_yaml, encoding="utf-8")

        env_refs: set[str] = set()
        collect_env_vars(config, env_refs)

        files = {CONFIG_NAME: _sha(config_yaml)}

        if include_flows:
            from agentplatform.flow import load_phase_graph
            from agentplatform.flow.codegen import flow_outputs

            graph = load_phase_graph(snap.root)
            for rel, text in sorted(flow_outputs(snap, graph).items()):
                target = out / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
                files[rel] = _sha(text)
            notes = {**notes, "phase_graph_digest": graph.digest}

        manifest = RenderManifest(
            spec_digest=snap.digest,
            renderer_version=agentplatform.__version__,
            files=files,
            env_refs=tuple(sorted(env_refs)),
            notes=notes,
        )
        manifest.write(out)
        return manifest


def _sha(text: str) -> str:
    from agentplatform.spec.fingerprint import sha256_hex

    return sha256_hex(text)


def render_workspace(snap: SpecSnapshot, out_dir: str | Path) -> RenderManifest:
    return Renderer().render(snap, out_dir)
