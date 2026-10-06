"""结构化 trace。

每次 LLM 请求、每次工具调用都会落成一行 JSON 写进 trace.jsonl。
它同时承担三个角色：
  1. 演示时让人看清 agent 「想一步、做一步」的完整链路；
  2. 出问题时能定位到具体是哪一轮、哪个工具；
  3. 把 token 与耗时显式记下来，成本可见。
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from typing import IO, Any

__all__ = ["Tracer"]


class Tracer:
    def __init__(
        self,
        path: Path | None,
        *,
        verbose: bool = True,
        stream: IO[str] | None = None,
        run_id: str | None = None,
    ) -> None:
        self._path = path
        self._verbose = verbose
        self._stream = stream if stream is not None else sys.stdout
        self._fh: IO[str] | None = None

        # trace.jsonl 是追加写入的：多次运行共用一个文件，靠 run 区分是哪一次
        self.run_id = run_id or uuid.uuid4().hex[:8]
        self.step = 1
        self.prompt_tokens = 0
        self.completion_tokens = 0

    # ---- 记录 ----------------------------------------------------------

    def event(self, kind: str, **payload: Any) -> dict[str, Any]:
        """记录一个事件，返回落盘的原始记录（测试里可直接断言）。"""
        record: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "run": self.run_id,
            "step": self.step,
            "event": kind,
            **payload,
        }
        self._write(record)
        if self._verbose:
            self._print(record)
        return record

    def next_step(self) -> None:
        self.step += 1

    def add_usage(self, usage: dict[str, int] | None) -> None:
        if not usage:
            return
        self.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        self.completion_tokens += int(usage.get("completion_tokens", 0) or 0)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "Tracer":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- 内部 ----------------------------------------------------------

    def _write(self, record: dict[str, Any]) -> None:
        if self._path is None:
            return
        if self._fh is None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self._path.open("a", encoding="utf-8")
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def _print(self, record: dict[str, Any]) -> None:
        step = record["step"]
        kind = record["event"]
        detail = _summarize(record)
        print(f"[step {step:>2}] {kind:<12} {detail}", file=self._stream)


_MAX_INLINE = 160


def _summarize(record: dict[str, Any]) -> str:
    """把一条记录压成一行人话，避免终端被长文本刷屏。"""
    kind = record["event"]
    if kind == "llm_request":
        return f"messages={record.get('messages', '?')} 条"
    if kind == "llm_response":
        calls = record.get("tool_calls") or []
        if calls:
            return "tool_calls=" + ", ".join(str(c) for c in calls)
        return f"text={_clip(str(record.get('content') or ''))}"
    if kind == "tool_call":
        return f"{record.get('tool')}({_clip(json.dumps(record.get('args', {}), ensure_ascii=False), 100)})"
    if kind == "tool_result":
        status = "ok" if record.get("ok") else "ERROR"
        return f"{record.get('tool')} -> {status}: {_clip(str(record.get('result') or record.get('error') or ''))}"
    if kind == "finish":
        return f"{record.get('reason')}: {_clip(str(record.get('summary') or ''))}"
    if kind == "error":
        return _clip(str(record.get("error") or ""))
    if kind == "run_start":
        return f"run={record.get('run')} root={record.get('root')} model={record.get('model')}"
    return _clip(json.dumps(record, ensure_ascii=False))


def _clip(text: str, limit: int = _MAX_INLINE) -> str:
    text = text.replace("\n", "\\n")
    if len(text) <= limit:
        return text
    return text[:limit] + f"…(+{len(text) - limit})"
