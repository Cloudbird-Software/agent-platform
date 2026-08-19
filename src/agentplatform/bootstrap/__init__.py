"""bootstrap/：开箱即用面（ADR-0025）——clone 后三步可用：init → 填 .env → up。

- doctor    自检清单（env 凭据/渲染一致性/账本完整性/runtime 可用性）
- envfile   manifest.env_refs → .env.example 生成（配 API 的唯一手工步骤）
- init      workspace 初始化（渲染 + state 创建 + envfile）——幂等
- paths     默认声明源（vendor/agent-registry 快照，零参数 init）
- up        执行团队流（live 全挂点 / dry-run 零调用预检）
"""

from agentplatform.bootstrap.doctor import run_doctor
from agentplatform.bootstrap.envfile import render_envfile
from agentplatform.bootstrap.init import init_workspace
from agentplatform.bootstrap.paths import default_registry
from agentplatform.bootstrap.up import UpError, run_up

__all__ = [
    "UpError",
    "default_registry",
    "init_workspace",
    "render_envfile",
    "run_doctor",
    "run_up",
]
