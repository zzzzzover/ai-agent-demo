"""工具层：声明、注册、执行。

新增一个工具只需要三步：写一个 `handler(ctx, **args) -> str`、写它的 JSON Schema、
在下面 `build_registry` 里挂上。循环层完全不需要改动。
"""

from __future__ import annotations

from . import files, finish, search, shell
from .finish import FINISH_TOOL
from .registry import Tool, ToolContext, ToolRegistry

__all__ = ["Tool", "ToolContext", "ToolRegistry", "FINISH_TOOL", "build_registry"]


def build_registry(ctx: ToolContext) -> ToolRegistry:
    tools: list[Tool] = [
        *files.make_tools(),
        *search.make_tools(),
        *shell.make_tools(),
        *finish.make_tools(),
    ]
    return ToolRegistry(ctx, tools)
