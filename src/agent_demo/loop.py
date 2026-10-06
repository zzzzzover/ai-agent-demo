"""Agent 主循环。

这是整个项目的核心，全部逻辑不到 150 行，可以逐行讲：

    组装 messages -> 问 LLM -> 有 tool_calls 就执行 -> 结果回灌 -> 再问

三个容易被忽略、但值得单独讲清楚的设计点：

1. **终止条件显式化**。四种出口各有自己的 status，能区分「它说做完了」
   和「它被 max_steps 掐断了」——后者必须如实报告失败，不能假装成功。
2. **错误恢复**。工具报错不中断循环，而是把结构化错误回灌给模型让它自己纠正；
   这才是 agent 相对固定 pipeline 的价值所在。
3. **死循环检测**。模型反复用同一参数调同一个工具时，及时掐断，避免白烧 token。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .llm import LLM, LLMError, LLMResponse
from .tools import FINISH_TOOL, ToolRegistry
from .trace import Tracer

__all__ = ["AgentLoop", "AgentResult", "SYSTEM_PROMPT"]

# 四种出口
FINISHED = "finished"
NO_TOOL_CALLS = "no_tool_calls"
MAX_STEPS = "max_steps"
LOOP_DETECTED = "loop_detected"
FAILED = "failed"

SYSTEM_PROMPT = """你是一个资料整理 agent，在一个受限的工作目录里工作。

规则：
1. 先探查再动手。不清楚资料分布时，先用 list_files 看清目录结构。
2. 不要凭空编造内容。所有结论都必须来自你实际读过的文件，并在报告中标注来源
   （用 `文件名:行号` 的形式，行号就是 read_file 返回的行号）。
3. 找不到需要的信息时，如实说明「未找到」，不要用常识填补。
4. 只读原始资料、只写 --out-dir 目录。写文件用 write_file。
5. 收到工具报错时，读 error 与 hint 字段，换一种方式重试，不要重复同样的调用。
6. 信息足够时就停手，调用 finish 结束任务，summary 里写清产出文件路径与核心结论。
   不要在资料明显不足时反复尝试同一个动作。
"""


@dataclass
class AgentResult:
    status: str
    summary: str
    steps: int
    messages: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in (FINISHED, NO_TOOL_CALLS)


class AgentLoop:
    def __init__(
        self,
        llm: LLM,
        registry: ToolRegistry,
        tracer: Tracer,
        *,
        max_steps: int = 12,
        repeat_limit: int = 3,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps 必须 >= 1")
        self.llm = llm
        self.registry = registry
        self.tracer = tracer
        self.max_steps = max_steps
        self.repeat_limit = repeat_limit

    # ---- 主流程 --------------------------------------------------------

    def run(self, task: str) -> AgentResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]
        tools = self.registry.schemas()

        last_signature: str | None = None
        repeats = 0

        for step in range(1, self.max_steps + 1):
            self.tracer.step = step
            self.tracer.event("llm_request", messages=len(messages))

            try:
                response = self.llm.chat(messages, tools)
            except LLMError as exc:
                self.tracer.event("error", error=str(exc), phase="llm")
                return AgentResult(FAILED, str(exc), step, messages)
            except Exception as exc:  # 网络层之外的意外也要如实收尾
                self.tracer.event("error", error=f"{type(exc).__name__}: {exc}", phase="llm")
                return AgentResult(FAILED, f"{type(exc).__name__}: {exc}", step, messages)

            self.tracer.add_usage(response.usage)
            self.tracer.event(
                "llm_response",
                content=response.content,
                tool_calls=[call.name for call in response.tool_calls],
                usage=response.usage,
            )

            # 出口一：模型不再调用工具，直接用文本作答
            if not response.tool_calls:
                summary = response.content or ""
                self.tracer.event("finish", reason=NO_TOOL_CALLS, summary=summary)
                return AgentResult(NO_TOOL_CALLS, summary, step, messages)

            messages.append(_assistant_message(response))

            for call in response.tool_calls:
                # 死循环检测：同一工具 + 同一参数连续出现
                signature = _signature(call.name, call.arguments)
                repeats = repeats + 1 if signature == last_signature else 1
                last_signature = signature
                if repeats >= self.repeat_limit:
                    summary = (
                        f"检测到死循环：连续 {repeats} 次调用 {call.name} 且参数完全相同，已主动中止。"
                    )
                    self.tracer.event("finish", reason=LOOP_DETECTED, summary=summary)
                    return AgentResult(LOOP_DETECTED, summary, step, messages)

                ok, payload = self._execute(call)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": payload,
                    }
                )
                # 只有 finish 真正执行成功才算结束；参数不合法时照常把错误回灌给模型
                if call.name == FINISH_TOOL and ok:
                    summary = _finish_summary(payload)
                    self.tracer.event("finish", reason=FINISHED, summary=summary)
                    return AgentResult(FINISHED, summary, step, messages)

        # 出口四：步数耗尽。必须如实报告未完成，不能伪装成功
        summary = f"达到 max_steps={self.max_steps} 仍未调用 finish，任务未确认完成。"
        self.tracer.event("finish", reason=MAX_STEPS, summary=summary)
        return AgentResult(MAX_STEPS, summary, self.max_steps, messages)

    # ---- 工具执行 ------------------------------------------------------

    def _execute(self, call) -> tuple[bool, str]:
        """执行一次工具调用，返回 (是否成功, 回灌给模型的字符串)。"""
        try:
            args = call.parsed_arguments()
        except LLMError as exc:
            payload = json.dumps(
                {"ok": False, "error": str(exc), "hint": "把参数改成合法的 JSON 对象后重试。"},
                ensure_ascii=False,
            )
            self.tracer.event("tool_call", tool=call.name, args={}, invalid=True)
            self.tracer.event("tool_result", tool=call.name, ok=False, error=str(exc))
            return False, payload

        self.tracer.event("tool_call", tool=call.name, args=args)
        ok, payload = self.registry.dispatch(call.name, args)
        self.tracer.event(
            "tool_result",
            tool=call.name,
            ok=ok,
            result=payload if ok else None,
            error=None if ok else payload,
        )
        return ok, payload


def _finish_summary(payload: str) -> str:
    """finish 的 payload 是统一包装过的 JSON，对外只暴露 summary 本身。"""
    try:
        body = json.loads(payload)
    except json.JSONDecodeError:
        return payload
    if isinstance(body, dict) and body.get("ok"):
        return str(body.get("result") or "")
    return payload


def _assistant_message(response: LLMResponse) -> dict[str, Any]:
    """把归一化响应还原成 OpenAI 协议里的 assistant 消息。"""
    return {
        "role": "assistant",
        "content": response.content or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in response.tool_calls
        ],
    }


def _signature(name: str, arguments: str) -> str:
    """把参数做一次规范化，避免仅空白/键序不同就被当成不同调用。"""
    try:
        parsed = json.loads(arguments) if arguments.strip() else {}
        normalized = json.dumps(parsed, sort_keys=True, ensure_ascii=False)
    except json.JSONDecodeError:
        normalized = arguments.strip()
    return f"{name}:{normalized}"
