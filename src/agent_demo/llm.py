"""LLM 客户端。

只做一件事：把 DeepSeek 的 OpenAI 兼容接口，归一化成循环层能直接消费的结构。
归一化带来的好处是 `loop.py` 不关心 HTTP、不关心重试，测试里换成 FakeLLM 即可离线跑。

接口本身是公开协议，参考 https://api-docs.deepseek.com/
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, MutableMapping, Protocol

import httpx

__all__ = ["ToolCall", "LLMResponse", "LLM", "DeepSeekLLM", "LLMError", "sanitize_no_proxy"]


class LLMError(Exception):
    """不可恢复的模型调用错误。"""


_BRACKETED_IPV6 = re.compile(r"^\[.*\]$")


def sanitize_no_proxy(env: MutableMapping[str, str] | None = None) -> list[str]:
    """绕开 httpx 的一个坑。

    httpx 0.28 在构造 Client 时会解析 no_proxy，遇到 `[::1]` 这种带方括号的 IPv6 字面量
    会直接抛 `InvalidURL: Invalid port: ':1]'`——注意是在**构造客户端时**就炸，跟请求无关。
    有些发行版/工具链默认就会写进这个条目。

    这里把方括号条目剔除。它们只影响「哪些地址不走代理」的白名单，去掉后对本项目
    没有实际影响（真正需要直连的是 localhost，那条本来就在）。返回被剔除的条目。
    """
    env = os.environ if env is None else env
    removed: list[str] = []
    for key in ("no_proxy", "NO_PROXY"):
        raw = env.get(key)
        if not raw:
            continue
        kept = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            if _BRACKETED_IPV6.match(part):
                removed.append(part)
                continue
            kept.append(part)
        env[key] = ",".join(kept)
    return removed


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # 模型返回的是 JSON 字符串，由循环层负责解析

    def parsed_arguments(self) -> dict[str, Any]:
        if not self.arguments.strip():
            return {}
        try:
            data = json.loads(self.arguments)
        except json.JSONDecodeError as exc:
            raise LLMError(f"工具 {self.name} 的参数不是合法 JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise LLMError(f"工具 {self.name} 的参数必须是 JSON 对象")
        return data


@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class LLM(Protocol):
    """循环层依赖的最小协议。测试用 FakeLLM 实现它。"""

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse: ...


class DeepSeekLLM:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        timeout: float = 60.0,
        max_retries: int = 3,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise LLMError(
                "缺少 DEEPSEEK_API_KEY。设置方式：\n"
                "  export DEEPSEEK_API_KEY=sk-...\n"
                "或复制 .env.example 为 .env 并填入。"
            )
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = client
        # 必须在任何 httpx.Client 被构造之前执行，否则会在构造阶段就抛 InvalidURL
        sanitize_no_proxy()

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        data = self._post_with_retry(payload)
        return self._normalize(data)

    # ---- 内部 ----------------------------------------------------------

    def _post_with_retry(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        client = self._client or httpx.Client(timeout=self.timeout)
        owns_client = self._client is None

        last_error: Exception | None = None
        try:
            for attempt in range(self.max_retries):
                try:
                    resp = client.post(url, headers=headers, json=payload)
                except httpx.TransportError as exc:
                    last_error = exc
                else:
                    if resp.status_code == 200:
                        return resp.json()
                    # 429 与 5xx 值得重试；4xx（除 429）是请求本身有问题，重试无用
                    if resp.status_code != 429 and resp.status_code < 500:
                        raise LLMError(
                            f"模型接口返回 {resp.status_code}: {resp.text[:400]}"
                        )
                    last_error = LLMError(
                        f"模型接口返回 {resp.status_code}: {resp.text[:200]}"
                    )

                if attempt < self.max_retries - 1:
                    time.sleep(2**attempt)  # 1s, 2s, 4s 指数退避

            raise LLMError(f"模型调用连续失败 {self.max_retries} 次: {last_error}")
        finally:
            if owns_client:
                client.close()

    @staticmethod
    def _normalize(data: dict[str, Any]) -> LLMResponse:
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"模型响应结构异常: {json.dumps(data)[:400]}") from exc

        calls = [
            ToolCall(
                id=str(item.get("id") or f"call_{idx}"),
                name=str(item.get("function", {}).get("name") or ""),
                arguments=str(item.get("function", {}).get("arguments") or ""),
            )
            for idx, item in enumerate(message.get("tool_calls") or [])
        ]
        usage = {
            k: int(v)
            for k, v in (data.get("usage") or {}).items()
            if isinstance(v, int)
        }
        return LLMResponse(
            content=message.get("content"),
            tool_calls=calls,
            usage=usage,
            raw=data,
        )
