"""Agent 主循环的离线回放测试。

覆盖四种出口 + 错误恢复 + 死循环检测。
"""

from __future__ import annotations

import json

from agent_demo.llm import LLMError
from agent_demo.loop import (
    FAILED,
    FINISHED,
    LOOP_DETECTED,
    MAX_STEPS,
    NO_TOOL_CALLS,
    AgentLoop,
)

from conftest import FakeLLM, call, text


def make_loop(env, script, **kwargs) -> tuple[AgentLoop, FakeLLM]:
    llm = FakeLLM(script)
    loop = AgentLoop(llm, env.registry, env.tracer, **kwargs)
    return loop, llm


def test_normal_completion(env):
    """完整走一遍：列目录 -> 读文件 -> 写报告 -> finish。"""
    loop, _ = make_loop(
        env,
        [
            call("list_files", {"path": "."}),
            call("read_file", {"path": "notes/alpha.md"}),
            call(
                "write_file",
                {"path": "report.md", "content": "# 报告\n\n阿尔法项目启动于 2024 年（notes/alpha.md:3）"},
            ),
            call("finish", {"summary": "已生成 report.md"}),
        ],
    )

    result = loop.run("把 notes 整理成报告")

    assert result.status == FINISHED
    assert result.steps == 4
    # finish 的 summary 应当是对人可读的纯文本，而不是包了一层 JSON
    assert result.summary == "已生成 report.md"
    assert (env.root / "agent_out" / "report.md").read_text(encoding="utf-8").startswith("# 报告")

    # 观察链路完整：每次工具结果都以 role=tool 回灌
    tool_messages = [m for m in result.messages if m["role"] == "tool"]
    assert len(tool_messages) == 4
    first_payload = json.loads(tool_messages[0]["content"])
    assert first_payload["ok"] is True
    assert "notes/" in first_payload["result"]

    read_payload = json.loads(tool_messages[1]["content"])
    assert "notes/alpha.md" in read_payload["result"]
    assert "2024" in read_payload["result"]


def test_tool_error_is_recovered_by_model(env):
    """工具报错不能中断循环，必须以结构化错误回灌，让模型自己换个做法。"""
    loop, llm = make_loop(
        env,
        [
            call("read_file", {"path": "notes/缺失.md"}),
            call("list_files", {"path": "notes"}),
            call("finish", {"summary": "改用 list_files 后完成"}),
        ],
    )

    result = loop.run("读 notes 里的资料")

    assert result.status == FINISHED
    error_payload = json.loads(llm.calls[1]["messages"][-1]["content"])
    assert error_payload["ok"] is False
    assert "error" in error_payload and "hint" in error_payload


def test_sandbox_violation_is_reported_to_model(env):
    """越界访问同样只是「一次失败的工具调用」，而不是崩溃。"""
    loop, llm = make_loop(
        env,
        [
            call("read_file", {"path": "../../../etc/passwd"}),
            call("finish", {"summary": "越界被拒，改用授权目录"}),
        ],
    )

    result = loop.run("读系统文件")

    assert result.status == FINISHED
    payload = json.loads(llm.calls[1]["messages"][-1]["content"])
    assert payload["ok"] is False
    assert "安全边界" in payload["hint"]


def test_max_steps_reports_failure_honestly(env):
    """步数耗尽必须报「未完成」，不能伪装成功。"""
    # 参数各不相同，避免被死循环检测（那是另一条出口）抢先拦下
    script = [call("list_files", {"path": ".", "max_entries": n}) for n in range(1, 6)]
    loop, _ = make_loop(env, script, max_steps=3)

    result = loop.run("一直列目录")

    assert result.status == MAX_STEPS
    assert not result.ok
    assert "未确认完成" in result.summary


def test_loop_detection_stops_repeated_identical_calls(env):
    """同一个工具 + 完全相同的参数，连续 repeat_limit 次就掐断。"""
    script = [call("list_files", {"path": "notes"}) for _ in range(3)]
    loop, _ = make_loop(env, script, max_steps=10, repeat_limit=3)

    result = loop.run("反复列同一个目录")

    assert result.status == LOOP_DETECTED
    assert "死循环" in result.summary


def test_repeated_call_with_different_args_is_allowed(env):
    """参数不同不算死循环——正常的多次读取不能被误判。"""
    loop, _ = make_loop(
        env,
        [
            call("read_file", {"path": "notes/alpha.md"}),
            call("read_file", {"path": "notes/beta.md"}),
            call("finish", {"summary": "读完两个文件"}),
        ],
        repeat_limit=2,
    )

    assert loop.run("依次读两个文件").status == FINISHED


def test_text_only_response_ends_loop(env):
    loop, _ = make_loop(env, [text("资料不足，无法整理。")])

    result = loop.run("整理资料")

    assert result.status == NO_TOOL_CALLS
    assert result.ok
    assert "资料不足" in result.summary


def test_llm_error_is_reported(env):
    loop, _ = make_loop(env, [LLMError("接口 500")])

    result = loop.run("随便做点什么")

    assert result.status == FAILED
    assert "500" in result.summary


def test_invalid_json_arguments_are_reported_back(env):
    """模型给出坏参数时，循环要把它当作可恢复的错误。"""
    from agent_demo.llm import LLMResponse, ToolCall

    bad = LLMResponse(
        tool_calls=[ToolCall(id="c1", name="read_file", arguments="{不是 JSON")],
        usage={},
    )
    loop, llm = make_loop(env, [bad, call("finish", {"summary": "改正后完成"})])

    result = loop.run("读文件")

    assert result.status == FINISHED
    payload = json.loads(llm.calls[1]["messages"][-1]["content"])
    assert payload["ok"] is False
    assert "hint" in payload


def test_finish_with_bad_arguments_does_not_end_the_loop(env):
    """finish 参数不合法时必须当成一次失败的调用，而不是当作任务完成。"""
    from agent_demo.llm import LLMResponse, ToolCall

    bad_finish = LLMResponse(
        tool_calls=[ToolCall(id="c1", name="finish", arguments="{}")],  # 缺 summary
        usage={},
    )
    loop, llm = make_loop(env, [bad_finish, call("finish", {"summary": "第二次带上了参数"})])

    result = loop.run("收尾")

    assert result.status == FINISHED
    assert result.summary == "第二次带上了参数"
    first_payload = json.loads(llm.calls[1]["messages"][-1]["content"])
    assert first_payload["ok"] is False
    assert "缺少必填参数" in first_payload["error"]


def test_trace_records_every_step(env):
    """trace 必须能重建「模型要什么、工具做了什么」。"""
    loop, _ = make_loop(
        env,
        [
            call("list_files", {"path": "."}),
            call("finish", {"summary": "完成"}),
        ],
    )

    loop.run("列目录")
    env.tracer.close()

    lines = (env.root / "agent_out" / "trace.jsonl").read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line)["event"] for line in lines]
    assert events == [
        "llm_request",
        "llm_response",
        "tool_call",
        "tool_result",
        "llm_request",
        "llm_response",
        "tool_call",
        "tool_result",
        "finish",
    ]

    # 人读的进度也要真的打出来了
    printed = env.stream.getvalue()
    assert "tool_call" in printed and "finish" in printed


def test_trace_records_carry_the_run_id(env):
    """trace.jsonl 是追加写入的，必须靠 run 字段区分是哪一次运行。"""
    from agent_demo.trace import Tracer

    loop, _ = make_loop(env, [call("finish", {"summary": "第一次"})])
    loop.run("第一次")

    second = Tracer(env.root / "agent_out" / "trace.jsonl", verbose=False)
    AgentLoop(FakeLLM([call("finish", {"summary": "第二次"})]), env.registry, second).run("第二次")
    env.tracer.close()
    second.close()

    records = [
        json.loads(line)
        for line in (env.root / "agent_out" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    runs = {record["run"] for record in records}
    assert len(runs) == 2, "两次运行必须落到不同的 run 标识上"
    assert all("run" in record for record in records)


def test_token_usage_accumulates(env):
    loop, _ = make_loop(
        env,
        [call("list_files", {"path": "."}), call("finish", {"summary": "完成"})],
    )

    loop.run("列目录")

    assert env.tracer.prompt_tokens == 2
    assert env.tracer.completion_tokens == 2
    assert env.tracer.total_tokens == 4
