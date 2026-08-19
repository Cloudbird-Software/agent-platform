"""spec/ 声明加载层：registry 快照 → 可指纹化的内存视图（ADR-0025）。

不变式（drift/render 依赖，对抗测试覆盖）：
- 快照不可变：加载后任何入口不得改写实体内容；
- 指纹路径绑定：file_digest = sha256(相对路径 + \0 + 规范化内容)——
  移动/改名同内容文件视为变更（防"内容没变就不算漂移"的绕过）；
- fail-closed：引用缺失/重复 id 即报错；status != approved 的实体可以存在于
  快照（草稿是声明面合法状态），但被 approved 实体引用或进入渲染面时报错——
  与 agent-registry validate.py 的"引用 status != approved 即拒绝"语义一致
  （静默跳过 = 渲染面与声明面隐性分叉，漂移的根源）。
"""

from agentplatform.spec.errors import SpecError
from agentplatform.spec.fingerprint import canonical_json, file_digest, sha256_hex
from agentplatform.spec.loader import RegistryLoader, SpecSnapshot

__all__ = [
    "RegistryLoader",
    "SpecError",
    "SpecSnapshot",
    "canonical_json",
    "file_digest",
    "sha256_hex",
]
