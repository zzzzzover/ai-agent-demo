"""工具行为测试：正常路径、边界与截断。"""

from __future__ import annotations

import json

from agent_demo.tools import files


# ---- list_files --------------------------------------------------------


def test_list_files_lists_dirs_and_files(env):
    result = files.list_files(env.ctx, path=".")

    assert "notes/" in result
    assert "README.txt" in result
    assert "共" in result


def test_list_files_on_file_is_an_error(env):
    ok, payload = env.registry.dispatch("list_files", {"path": "README.txt"})

    assert ok is False
    assert "不是目录" in json.loads(payload)["error"]


# ---- read_file ---------------------------------------------------------


def test_read_file_numbers_lines(env):
    result = files.read_file(env.ctx, path="notes/alpha.md")

    assert "# notes/alpha.md" in result
    assert "第 1-4 行 / 共 4 行" in result
    assert "   1| # Alpha" in result
    assert "2024" in result


def test_read_file_supports_continuation(env):
    result = files.read_file(env.ctx, path="notes/alpha.md", start_line=3, max_lines=2)

    assert "第 3-4 行" in result
    assert "   3| " in result
    assert "# Alpha" not in result


def test_read_file_tells_model_it_was_truncated(env):
    result = files.read_file(env.ctx, path="notes/alpha.md", max_lines=2)

    assert "第 1-2 行 / 共 4 行" in result
    assert "后续还有 2 行" in result


def test_read_file_missing_file_is_recoverable(env):
    ok, payload = env.registry.dispatch("read_file", {"path": "notes/nope.md"})

    assert ok is False
    body = json.loads(payload)
    assert body["ok"] is False
    assert "list_files" in body["hint"]


# ---- search_text -------------------------------------------------------


def test_search_text_finds_matches_with_line_numbers(env):
    ok, payload = env.registry.dispatch("search_text", {"pattern": "负责人", "path": "."})

    assert ok is True
    body = json.loads(payload)["result"]
    assert "notes/alpha.md:3" in body
    assert "notes/beta.md:3" in body


def test_search_text_reports_no_match(env):
    ok, payload = env.registry.dispatch("search_text", {"pattern": "不存在的词"})

    assert ok is True
    assert "没有匹配" in json.loads(payload)["result"]


def test_search_text_rejects_bad_regex(env):
    ok, payload = env.registry.dispatch("search_text", {"pattern": "([unclosed"})

    assert ok is False
    assert "正则" in json.loads(payload)["error"]


def test_search_text_skips_out_dir(env):
    """agent 自己写的产物不该反过来污染后续搜索。"""
    (env.root / "agent_out").mkdir(exist_ok=True)
    (env.root / "agent_out" / "report.md").write_text("负责人：王五\n", encoding="utf-8")

    ok, payload = env.registry.dispatch("search_text", {"pattern": "负责人"})

    assert ok is True
    assert "王五" not in json.loads(payload)["result"]


# ---- registry 协议层 ---------------------------------------------------


def test_unknown_tool_is_rejected(env):
    ok, payload = env.registry.dispatch("fly_to_moon", {})

    assert ok is False
    body = json.loads(payload)
    assert "未知工具" in body["error"]
    assert "list_files" in body["hint"]


def test_missing_required_argument_is_rejected(env):
    ok, payload = env.registry.dispatch("read_file", {})

    assert ok is False
    assert "缺少必填参数" in json.loads(payload)["error"]


def test_schemas_are_openai_tool_declarations(env):
    schemas = env.registry.schemas()

    assert {s["function"]["name"] for s in schemas} == {
        "list_files",
        "read_file",
        "write_file",
        "search_text",
        "run_command",
        "finish",
    }
    for schema in schemas:
        assert schema["type"] == "function"
        assert schema["function"]["parameters"]["type"] == "object"
        assert schema["function"]["description"]
