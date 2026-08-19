"""init：workspace 初始化——渲染 + state 创建 + .env.example（幂等）。

幂等语义：
- 渲染：clean 重渲染（输出面是声明投影，可随时重建）；
- state：只创建不覆盖（账本是唯一事实源——append-only，绝不清洗）；
- .env.example：覆盖生成（模板）；.env 本体若存在则不动。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentplatform.bootstrap.envfile import render_envfile
from agentplatform.bootstrap.paths import default_registry
from agentplatform.observe.store import RuntimeStore
from agentplatform.render import Renderer
from agentplatform.render.manifest import RenderManifest
from agentplatform.spec import RegistryLoader

ENV_EXAMPLE = ".env.example"


def init_workspace(
    registry: str | Path | None,
    out: str | Path,
    *,
    envelope_usd: float = 100.0,
    overhead_usd: float = 20.0,
    state: str | Path | None = None,
) -> dict[str, Any]:
    """初始化 workspace；返回摘要（manifest 摘要+state 路径+env 模板路径）。

    registry=None 时用默认声明源（vendor/agent-registry 快照）。
    """
    reg = Path(registry) if registry else default_registry()
    if reg is None:
        raise FileNotFoundError("默认声明源不存在（vendor/agent-registry 缺失）——显式传 --registry")
    snap = RegistryLoader().load(reg)
    manifest: RenderManifest = Renderer().render(snap, out)

    ws = Path(out)
    env_example = ws / ENV_EXAMPLE
    env_example.write_text(render_envfile(ws), encoding="utf-8")

    state_dir = Path(state) if state else ws / "state"
    created = False
    if not (state_dir / "ledger.jsonl").is_file():
        RuntimeStore.create(state_dir, envelope_usd=envelope_usd, overhead_usd=overhead_usd)
        created = True

    return {
        "workspace": str(ws),
        "spec_digest": manifest.spec_digest[:16],
        "manifest_digest": manifest.digest[:16],
        "files": len(manifest.files),
        "flows": sorted(k for k in manifest.files if k.startswith("swarmflow/")),
        "env_example": str(env_example),
        "env_refs": list(manifest.env_refs),
        "state_dir": str(state_dir),
        "state_created": created,
        "next": [
            f"cp {env_example} {ws}/.env && 填入凭据",
            f"ap doctor --workspace {ws}" + (f" --registry {registry}" if registry else ""),
            f"ap tui --state {state_dir}",
        ],
    }
