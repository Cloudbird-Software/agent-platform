"""render/ 渲染器：声明快照 → jiuwenswarm workspace（工具中立，ADR-0025）。

一致性模型（用户核心关切）：
- 渲染是纯函数：输出只依赖 (spec_digest, renderer_version)——同一声明两次
  渲染字节一致（manifest.files 的 sha256 相同）；时间戳/随机数不进入输出；
- manifest.json 是输出面的指纹账本：files{path: sha256} + spec_digest +
  env_refs（部署者需要提供的变量清单）；`ap render --check`（drift/）重算
  哈希对账——运行面被手改即漂移；
- env: 引用透传为 ${VAR}（jiuwenswarm 原生展开形式），密钥永不落盘。
"""

from agentplatform.render.manifest import RenderManifest, load_manifest
from agentplatform.render.renderer import Renderer, render_workspace

__all__ = ["RenderManifest", "Renderer", "load_manifest", "render_workspace"]
