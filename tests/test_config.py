"""配置解析测试：命令行 > 环境变量 > 默认值。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_demo.config import DEFAULT_BASE_URL, DEFAULT_MODEL, Config, load_env_file

ENV_KEYS = ("DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "AGENT_MODEL")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_load_env_file_reads_key_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# 注释\nDEEPSEEK_API_KEY="sk-from-file"\n\nAGENT_MODEL=deepseek-reasoner\n',
        encoding="utf-8",
    )

    load_env_file(env_file)

    assert os.environ["DEEPSEEK_API_KEY"] == "sk-from-file"  # 引号被剥掉
    assert os.environ["AGENT_MODEL"] == "deepseek-reasoner"


def test_load_env_file_does_not_override_existing(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-already-set")
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=sk-from-file\n", encoding="utf-8")

    load_env_file(env_file)

    assert os.environ["DEEPSEEK_API_KEY"] == "sk-already-set"


def test_load_env_file_missing_file_is_silent(tmp_path):
    load_env_file(tmp_path / "不存在.env")  # 不应抛异常


def test_defaults_are_used_when_nothing_is_set(tmp_path):
    cfg = Config.from_env(root=tmp_path, task="t")

    assert cfg.model == DEFAULT_MODEL
    assert cfg.base_url == DEFAULT_BASE_URL
    assert cfg.api_key == ""
    assert cfg.out_dir == "agent_out"
    assert cfg.max_steps == 12
    assert cfg.allow_cmds == ()


def test_env_vars_fill_in_when_cli_is_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
    monkeypatch.setenv("AGENT_MODEL", "deepseek-reasoner")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://example.invalid")

    cfg = Config.from_env(root=tmp_path, task="t")

    assert cfg.api_key == "sk-env"
    assert cfg.model == "deepseek-reasoner"
    assert cfg.base_url == "https://example.invalid"


def test_cli_values_win_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MODEL", "deepseek-reasoner")

    cfg = Config.from_env(root=tmp_path, task="t", model="deepseek-chat")

    assert cfg.model == "deepseek-chat"


def test_root_is_normalized_to_path(tmp_path):
    cfg = Config.from_env(root=str(tmp_path), task="t")

    assert isinstance(cfg.root, Path)
    assert cfg.trace_path == tmp_path / "agent_out" / "trace.jsonl"


def test_allow_cmds_is_normalized_to_tuple(tmp_path):
    cfg = Config.from_env(root=tmp_path, task="t", allow_cmds=["wc", "head"])

    assert cfg.allow_cmds == ("wc", "head")
