"""finish：显式宣告任务结束。

给模型一个"正常退出"的动作，比让它靠"不再调用工具"来隐式结束更可靠：
我们总能区分「它认为做完了」和「它被 max_steps 掐断了」。
"""

from __future__ import annotations

from .registry import Tool, ToolContext

FINISH_TOOL = "finish"


def finish(ctx: ToolContext, summary: str) -> str:
    del ctx
    return summary


def make_tools() -> list[Tool]:
    return [
        Tool(
            name=FINISH_TOOL,
            description=(
                "任务完成时调用。summary 里写：做了什么、产出文件路径、结论。"
                "调用后循环立即结束。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "任务完成的总结"},
                },
                "required": ["summary"],
            },
            handler=finish,
        )
    ]
