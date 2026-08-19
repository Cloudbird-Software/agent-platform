"""规范化指纹——渲染确定性/漂移检测的密码学根基。

设计要点（对抗风险见 tests/test_spec_adversarial.py）：
- canonical_json：键排序 + 紧凑分隔符 + ensure_ascii——同一语义 dict 在
  任何加载顺序/缩进/注释差异下指纹一致（YAML 无规范形，规范化责任在这里）；
- 路径绑定：file_digest 把相对路径混入哈希输入（路径+\\0+内容），改名/移动
  必然变指纹——对抗"把声明挪个目录躲开漂移告警"；
- 排序聚合：snapshot 级摘要按 (路径, 指纹) 排序后串联——目录遍历顺序
  （平台相关）不影响结果。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_SEP = "\x00"


def canonical_json(obj: Any) -> str:
    """语义等价 → 字节等价：键排序、紧凑、unicode 直出。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def content_digest(obj: Any) -> str:
    """规范化内容的 sha256（实体级指纹）。"""
    return sha256_hex(canonical_json(obj))


def _normalize(obj: Any) -> Any:
    """加载即规范化：YAML 解析结果里唯一的不确定来源是 dict 键序——
    canonical_json 已排序，这里只剥离 None 尾巴不必做；保留原样最忠实。"""
    return obj


def file_digest(rel_path: str, obj: Any) -> str:
    """路径绑定指纹：sha256(rel_path + \\0 + canonical_json(obj))。"""
    return sha256_hex(rel_path + _SEP + canonical_json(_normalize(obj)))


def digest_of_files(entries: list[tuple[str, str]]) -> str:
    """[(相对路径, 文件指纹)] → 快照摘要（排序后哈希串联，序无关）。"""
    ordered = sorted(entries)
    return sha256_hex(canonical_json(ordered))


def hash_file(path: Path) -> str:
    """字节级文件哈希（二进制资源：prompt/前端产物等非 YAML 资源用）。"""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()
