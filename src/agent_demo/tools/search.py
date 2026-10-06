"""search_text：正则搜索。

刻意不依赖 ripgrep 等外部二进制——它们是环境依赖，会让「克隆下来就能跑」失效。
对一个 demo 规模的数据集，纯 Python 遍历完全够用。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .registry import Tool, ToolContext

MAX_FILE_BYTES = 1_000_000
MAX_FILES_SCANNED = 2_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".pytest_cache"}


def search_text(
    ctx: ToolContext,
    pattern: str,
    path: str = ".",
    max_results: int = 50,
) -> str:
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"正则表达式不合法: {exc}") from exc

    start = ctx.sandbox.resolve_read(path)
    if not start.exists():
        raise FileNotFoundError(f"{path} 不存在")

    max_results = max(1, int(max_results))
    out_dir = ctx.sandbox.out_dir

    hits: list[str] = []
    scanned = 0
    truncated = False

    for file_path in _iter_files(start, out_dir):
        scanned += 1
        if scanned > MAX_FILES_SCANNED:
            truncated = True
            break
        try:
            if file_path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append(f"{ctx.sandbox.rel(file_path)}:{lineno}: {line.strip()[:200]}")
                if len(hits) >= max_results:
                    truncated = True
                    break
        if truncated:
            break

    if not hits:
        return f"在 {ctx.sandbox.rel(start)} 下没有匹配 {pattern!r} 的内容"

    header = f"匹配 {pattern!r}：命中 {len(hits)} 条（已扫描 {scanned} 个文件）"
    if truncated:
        header += "，结果已截断"
    return header + "\n" + "\n".join(hits)


def _iter_files(start: Path, out_dir: Path):
    """遍历文本候选文件；不跟随符号链接，跳过产物目录与 --out-dir 自身。"""
    if start.is_file():
        yield start
        return

    for dirpath, dirnames, filenames in os.walk(start, followlinks=False):
        current = Path(os.path.abspath(dirpath))
        dirnames[:] = [
            name
            for name in dirnames
            if name not in SKIP_DIRS
            and not (current / name).is_symlink()
            and not (current / name).is_relative_to(out_dir)
        ]
        for name in filenames:
            full = current / name
            if full.is_symlink():
                continue
            yield full


def make_tools() -> list[Tool]:
    return [
        Tool(
            name="search_text",
            description=(
                "在目录下用正则搜索文本，返回「文件:行号: 内容」。"
                "用于在大量资料里快速定位与任务相关的段落。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python 正则表达式"},
                    "path": {
                        "type": "string",
                        "description": "搜索起点（目录或单个文件），相对 --root，默认 .",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最多返回多少条命中，默认 50",
                    },
                },
                "required": ["pattern"],
            },
            handler=search_text,
        )
    ]
