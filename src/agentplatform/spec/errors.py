"""声明加载错误分类——渲染面 fail-closed 的判别依据。

kind 面向机器（drift/CI 按类分流），message 面向人（中文，含路径/实体 id）。
"""

from __future__ import annotations


class SpecError(Exception):
    """声明面错误。kind ∈ {missing, parse, duplicate, reference, status, shape}"""

    kind: str = "shape"

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def missing(path: str, what: str = "声明文件") -> SpecError:
    return SpecError("missing", f"{what}缺失: {path}")


def parse_error(path: str, detail: str) -> SpecError:
    return SpecError("parse", f"YAML 解析失败: {path}: {detail}")


def duplicate(kind: str, entity_id: str, first: str, second: str) -> SpecError:
    return SpecError("duplicate", f"{kind} id 重复: {entity_id}（{first} 与 {second}）")


def reference(src: str, ref: str, target_kind: str) -> SpecError:
    return SpecError("reference", f"{src} 引用了不存在的 {target_kind}: {ref}")


def bad_status(entity_id: str, status: str) -> SpecError:
    return SpecError(
        "status",
        f"实体 {entity_id} status={status!r}——渲染面只接受 approved"
        "（非 approved 实体参与渲染 = 声明与执行分叉；请先在 registry 侧走完批准流）",
    )


def shape(path: str, detail: str) -> SpecError:
    return SpecError("shape", f"结构错误: {path}: {detail}")
