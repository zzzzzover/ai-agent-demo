"""文件类工具：list_files / read_file / write_file。

每个 handler 都返回**给模型看的字符串**。
参数校验与越界判断交给 sandbox，出错就抛异常，由注册表统一转成结构化错误回灌给模型。
"""

from __future__ import annotations

from .registry import Tool, ToolContext

MAX_READ_BYTES = 1_000_000  # 单文件读取上限 1MB，避免把上下文一次撑爆


def list_files(ctx: ToolContext, path: str = ".", max_entries: int = 200) -> str:
    target = ctx.sandbox.resolve_read(path)
    if not target.exists():
        raise FileNotFoundError(f"{path} 不存在")
    if not target.is_dir():
        raise NotADirectoryError(f"{path} 不是目录，读文件请用 read_file")

    entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    lines: list[str] = []
    for entry in entries[:max_entries]:
        rel = ctx.sandbox.rel(entry)
        if entry.is_dir():
            lines.append(f"dir   {rel}/")
        else:
            try:
                size = entry.stat().st_size
            except OSError:
                size = -1
            lines.append(f"file  {rel}  ({size} bytes)")

    header = f"{ctx.sandbox.rel(target)}/ 共 {len(entries)} 项"
    if len(entries) > max_entries:
        header += f"，仅显示前 {max_entries} 项"
    if not lines:
        return header + "\n(空目录)"
    return header + "\n" + "\n".join(lines)


def read_file(
    ctx: ToolContext,
    path: str,
    start_line: int = 1,
    max_lines: int = 200,
) -> str:
    target = ctx.sandbox.resolve_read(path)
    if not target.is_file():
        raise FileNotFoundError(f"{path} 不是可读文件")

    size = target.stat().st_size
    if size > MAX_READ_BYTES:
        raise ValueError(
            f"{path} 有 {size} 字节，超过 {MAX_READ_BYTES} 上限；"
            "请改用 search_text 定位，或分段读取"
        )

    start_line = max(1, int(start_line))
    max_lines = max(1, int(max_lines))
    text = target.read_text(encoding="utf-8", errors="replace")
    all_lines = text.splitlines()

    window = all_lines[start_line - 1 : start_line - 1 + max_lines]
    rel = ctx.sandbox.rel(target)
    if not window:
        return f"{rel} 在第 {start_line} 行之后没有内容（共 {len(all_lines)} 行）"

    numbered = "\n".join(
        f"{start_line + offset:>5}| {line}" for offset, line in enumerate(window)
    )
    end = start_line + len(window) - 1
    header = f"# {rel}  第 {start_line}-{end} 行 / 共 {len(all_lines)} 行"
    if end < len(all_lines):
        header += f"（后续还有 {len(all_lines) - end} 行，可调 start_line 继续读）"
    return f"{header}\n{numbered}"


def write_file(ctx: ToolContext, path: str, content: str) -> str:
    target = ctx.sandbox.resolve_write(path)
    rel = ctx.sandbox.rel(target)

    if ctx.dry_run:
        return f"[dry-run] 本应写入 {rel}（{len(content)} 字符），实际未落盘"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"已写入 {rel}（{len(content)} 字符）"


def make_tools() -> list[Tool]:
    return [
        Tool(
            name="list_files",
            description="列出目录内容。用于先摸清资料分布，再决定读哪些文件。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对 --root 的目录路径，默认 .",
                    },
                    "max_entries": {
                        "type": "integer",
                        "description": "最多列出多少项，默认 200",
                    },
                },
            },
            handler=list_files,
        ),
        Tool(
            name="read_file",
            description="按行读取一个文本文件。返回内容带行号，便于在报告里引用出处。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对 --root 的文件路径"},
                    "start_line": {"type": "integer", "description": "起始行号，从 1 开始"},
                    "max_lines": {"type": "integer", "description": "最多读多少行，默认 200"},
                },
                "required": ["path"],
            },
            handler=read_file,
        ),
        Tool(
            name="write_file",
            description=(
                "把内容写入文件。只能写到 --out-dir 目录下，不能改动原始资料。"
                "最终报告请写到这里。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对 --out-dir 的文件路径"},
                    "content": {"type": "string", "description": "要写入的完整文本"},
                },
                "required": ["path", "content"],
            },
            handler=write_file,
        ),
    ]
