"""LLM 层测试：响应归一化、参数解析、no_proxy 兼容处理。

全部离线，不发真实请求。
"""

from __future__ import annotations

import pytest

from agent_demo.llm import (
    DeepSeekLLM,
    LLMError,
    ToolCall,
    sanitize_no_proxy,
)


# ---- no_proxy 兼容处理 -------------------------------------------------


def test_sanitize_removes_bracketed_ipv6():
    env = {"no_proxy": "localhost,127.0.0.1,[::1]", "NO_PROXY": "[::1],*.local"}

    removed = sanitize_no_proxy(env)

    assert removed == ["[::1]", "[::1]"]
    assert env["no_proxy"] == "localhost,127.0.0.1"
    assert env["NO_PROXY"] == "*.local"


def test_sanitize_is_noop_when_clean():
    env = {"no_proxy": "localhost,127.0.0.1"}

    assert sanitize_no_proxy(env) == []
    assert env["no_proxy"] == "localhost,127.0.0.1"


def test_sanitize_tolerates_missing_and_empty():
    assert sanitize_no_proxy({}) == []
    assert sanitize_no_proxy({"no_proxy": ""}) == []


def test_httpx_client_can_be_constructed_after_sanitize(monkeypatch):
    """这是这条兼容处理存在的理由：不去掉 [::1]，Client 根本构造不出来。"""
    import httpx

    monkeypatch.setenv("no_proxy", "localhost,[::1]")

    sanitize_no_proxy()

    client = httpx.Client(timeout=1)
    client.close()


# ---- 构造与参数解析 ----------------------------------------------------


def test_missing_api_key_raises_with_actionable_message():
    with pytest.raises(LLMError, match="DEEPSEEK_API_KEY"):
        DeepSeekLLM("")


def test_tool_call_parses_json_arguments():
    call = ToolCall(id="c1", name="read_file", arguments='{"path": "a.md"}')

    assert call.parsed_arguments() == {"path": "a.md"}


def test_tool_call_empty_arguments_is_empty_dict():
    assert ToolCall(id="c1", name="finish", arguments="").parsed_arguments() == {}


@pytest.mark.parametrize("bad", ["{不是 JSON", "[1, 2]", '"字符串"'])
def test_tool_call_rejects_bad_arguments(bad):
    with pytest.raises(LLMError):
        ToolCall(id="c1", name="read_file", arguments=bad).parsed_arguments()


# ---- 响应归一化 --------------------------------------------------------


def test_normalize_extracts_tool_calls_and_usage():
    raw = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {"name": "list_files", "arguments": '{"path": "."}'},
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 18, "total_tokens": 138},
    }

    resp = DeepSeekLLM._normalize(raw)

    assert resp.content is None
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "list_files"
    assert resp.usage == {"prompt_tokens": 120, "completion_tokens": 18, "total_tokens": 138}


def test_normalize_handles_plain_text_reply():
    raw = {"choices": [{"message": {"content": "资料不够，无法整理。"}}]}

    resp = DeepSeekLLM._normalize(raw)

    assert resp.content == "资料不够，无法整理。"
    assert resp.tool_calls == []
    assert resp.usage == {}


def test_normalize_fills_missing_tool_call_id():
    raw = {
        "choices": [
            {"message": {"content": None, "tool_calls": [{"function": {"name": "finish"}}]}}
        ]
    }

    resp = DeepSeekLLM._normalize(raw)

    assert resp.tool_calls[0].id == "call_0"
    assert resp.tool_calls[0].arguments == ""


@pytest.mark.parametrize("raw", [{}, {"choices": []}, {"choices": [{}]}])
def test_normalize_rejects_malformed_response(raw):
    with pytest.raises(LLMError, match="结构异常"):
        DeepSeekLLM._normalize(raw)
