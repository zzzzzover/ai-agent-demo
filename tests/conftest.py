"""测试夹具。

核心是 `FakeLLM`：它实现 `LLM` 协议，按预置「剧本」逐轮返回响应。
于是整个 agent 循环可以在**不联网、不花一分钱 token** 的前提下被完整测试——

这是这个项目相对多数 agent demo 的关键差别：别人的 agent 只能靠「跑一次看看」验证，
我们的循环有确定性回归测试。模型换了、提示词改了，跑一遍测试就知道有没有退化。
"""

from __future__ import annotations

import copy
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import pytest

from agent_demo.llm import LLMResponse, ToolCall
from agent_demo.safety import CommandAllowlist, PathSandbox
from agent_demo.tools import ToolContext, build_registry
from agent_demo.trace import Tracer


# ---- 剧本构造器 --------------------------------------------------------


def text(content: str) -> LLMResponse:
    """模型直接用文本作答，不调用工具。"""
    return LLMResponse(content=content, usage={"prompt_tokens": 1, "completion_tokens": 1})


def call(name: str, args: dict[str, Any] | None = None, call_id: str | None = None) -> LLMResponse:
    """模型发起一次工具调用。"""
    import json

    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(
                id=call_id or f"call_{name}",
                name=name,
                arguments=json.dumps(args or {}, ensure_ascii=False),
            )
        ],
        usage={"prompt_tokens": 1, "completion_tokens": 1},
    )


class FakeLLM:
    """按剧本回放，并记录每一轮实际收到的 messages，供断言。"""

    def __init__(self, script: Iterable[LLMResponse | Exception]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, tools) -> LLMResponse:
        self.calls.append({"messages": copy.deepcopy(messages), "tools": tools})
        if not self.script:
            raise AssertionError("FakeLLM 的剧本已用完，但循环还在继续调用模型")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def last_messages(self) -> list[dict[str, Any]]:
        return self.calls[-1]["messages"]


# ---- 夹具 --------------------------------------------------------------


@dataclass
class Env:
    root: Path
    sandbox: PathSandbox
    ctx: ToolContext
    registry: Any
    tracer: Tracer
    stream: io.StringIO


@pytest.fixture
def env(tmp_path: Path) -> Env:
    """一个带示例资料的临时工作目录。"""
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "alpha.md").write_text(
        "# Alpha\n\n阿尔法项目于 2024 年启动，负责人是张三。\n第二行用于测试行号。\n",
        encoding="utf-8",
    )
    (notes / "beta.md").write_text(
        "# Beta\n\n贝塔项目于 2025 年启动，负责人是李四。\n",
        encoding="utf-8",
    )
    (tmp_path / "README.txt").write_text("根目录说明文件\n", encoding="utf-8")

    sandbox = PathSandbox(tmp_path, "agent_out")
    ctx = ToolContext(sandbox=sandbox, allowlist=CommandAllowlist([]), dry_run=False)
    stream = io.StringIO()
    tracer = Tracer(tmp_path / "agent_out" / "trace.jsonl", verbose=True, stream=stream)
    return Env(
        root=tmp_path,
        sandbox=sandbox,
        ctx=ctx,
        registry=build_registry(ctx),
        tracer=tracer,
        stream=stream,
    )
