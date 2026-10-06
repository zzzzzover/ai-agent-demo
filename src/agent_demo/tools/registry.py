"""工具注册表。

对外只有两件事：
  * `schemas()` —— 生成给模型看的 JSON Schema 工具声明；
  * `dispatch()` —— 按名字执行，并把任何异常转成**结构化错误**。

结构化错误是关键设计：工具失败不终止循环，而是把 `{"ok": false, "error": ..., "hint": ...}`
回灌给模型，让它自己换路径重试。这是 agent 与「一次性 SQL/pipeline」最本质的差别。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from ..safety import CommandAllowlist, PathSandbox, SandboxError

__all__ = ["Tool", "ToolContext", "ToolRegistry"]

Handler = Callable[..., str]

# 按异常类型给出可执行的下一步建议，比单纯报错有用得多
_HINTS: tuple[tuple[type[BaseException], str], ...] = (
    (SandboxError, "这是安全边界，不要尝试绕过。改用 --root 范围内的路径。"),
    (FileNotFoundError, "路径可能拼错了，先用 list_files 确认目录内容。"),
    (NotADirectoryError, "该路径不是目录，读文件请用 read_file。"),
    (IsADirectoryError, "该路径是目录，列目录请用 list_files。"),
    (PermissionError, "没有读取权限，换一个文件。"),
    (ValueError, "参数取值不合法，检查后重试。"),
    (TypeError, "参数类型不对，检查工具声明里的参数类型。"),
)


@dataclass(frozen=True)
class ToolContext:
    sandbox: PathSandbox
    allowlist: CommandAllowlist
    dry_run: bool = False


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Handler

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self, ctx: ToolContext, tools: list[Tool]) -> None:
        self.ctx = ctx
        self._tools = {tool.name: tool for tool in tools}

    # ---- 对外接口 ------------------------------------------------------

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def dispatch(self, name: str, args: dict[str, Any]) -> tuple[bool, str]:
        """执行工具。返回 (是否成功, 给模型看的字符串)。"""
        tool = self._tools.get(name)
        if tool is None:
            return False, _encode_error(
                f"未知工具 {name!r}", f"可用工具: {', '.join(self.names())}"
            )

        if not isinstance(args, dict):
            return False, _encode_error(
                f"工具 {name} 的参数必须是 JSON 对象", _hint_for(TypeError())
            )

        missing = [
            key
            for key in tool.parameters.get("required", [])
            if key not in args
        ]
        if missing:
            return False, _encode_error(
                f"工具 {name} 缺少必填参数: {', '.join(missing)}",
                _hint_for(TypeError()),
            )

        try:
            result = tool.handler(self.ctx, **args)
        except SandboxError as exc:
            return False, _encode_error(str(exc), _hint_for(exc))
        except Exception as exc:  # 工具失败不应终止循环，一律转成结构化错误
            return False, _encode_error(
                f"{type(exc).__name__}: {exc}", _hint_for(exc)
            )

        return True, json.dumps({"ok": True, "result": result}, ensure_ascii=False)


def _hint_for(exc: BaseException) -> str:
    for exc_type, hint in _HINTS:
        if isinstance(exc, exc_type):
            return hint
    return "调整参数后重试；若多次失败，改用其它工具完成同一目标。"


def _encode_error(error: str, hint: str) -> str:
    return json.dumps({"ok": False, "error": error, "hint": hint}, ensure_ascii=False)
