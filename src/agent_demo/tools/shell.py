"""run_command：受控的命令执行。

设计上刻意做得很保守：
  * 命令必须在 --allow-cmd 白名单里（默认空 = 完全禁用）；
  * 只接受裸命令名，不接受带路径的可执行文件；
  * shell=False —— `;`、`|`、`&&`、`>` 全部不会被解释；
  * 工作目录固定为 --root，超时 30 秒，输出截断。

它证明的是「我知道怎么把危险能力关进笼子」，而不是「我敢让模型跑命令」。
"""

from __future__ import annotations

import subprocess

from .registry import Tool, ToolContext

TIMEOUT_SECONDS = 30
MAX_OUTPUT_CHARS = 4_000


def run_command(ctx: ToolContext, cmd: str, args: list[str] | None = None) -> str:
    resolved = ctx.allowlist.resolve(cmd)
    argv = [str(a) for a in (args or [])]

    if ctx.dry_run:
        return f"[dry-run] 本应执行 {cmd} {' '.join(argv)}（cwd={ctx.sandbox.root}）"

    try:
        proc = subprocess.run(  # noqa: S603 - shell=False 且命令来自白名单
            [resolved, *argv],
            cwd=ctx.sandbox.root,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"{cmd} 执行超过 {TIMEOUT_SECONDS} 秒，已终止") from None

    parts = [f"exit_code={proc.returncode}"]
    if proc.stdout:
        parts.append("stdout:\n" + _clip(proc.stdout))
    if proc.stderr:
        parts.append("stderr:\n" + _clip(proc.stderr))
    if not proc.stdout and not proc.stderr:
        parts.append("(无输出)")
    return "\n".join(parts)


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n…(输出被截断，共 {len(text)} 字符)"


def make_tools() -> list[Tool]:
    return [
        Tool(
            name="run_command",
            description=(
                "执行白名单内的命令，cwd 固定为 --root。默认禁用；"
                "仅当命令行传了 --allow-cmd 时才可用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "裸命令名，例如 wc"},
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "参数列表，不含命令本身",
                    },
                },
                "required": ["cmd"],
            },
            handler=run_command,
        )
    ]
