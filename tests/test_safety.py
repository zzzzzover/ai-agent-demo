"""安全边界测试。

这组测试是回答「凭什么让 LLM 动你的文件系统」的证据。
每一条都对应一种真实的逃逸手法。
"""

from __future__ import annotations

import json

import pytest

from agent_demo.safety import CommandAllowlist, PathSandbox, SandboxError
from agent_demo.tools import ToolContext, build_registry


# ---- 路径沙箱 ----------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "../outside.txt",
        "notes/../../outside.txt",
        "/etc/passwd",
        "../../../etc/passwd",
        "notes/../..",
    ],
)
def test_read_outside_root_is_rejected(env, raw):
    with pytest.raises(SandboxError):
        env.sandbox.resolve_read(raw)


def test_absolute_path_inside_root_is_allowed(env):
    target = env.root / "notes" / "alpha.md"
    assert env.sandbox.resolve_read(str(target)) == target


def test_symlink_escaping_root_is_rejected(env):
    """软链接指向 root 之外时，resolve() 展开后必须被拦住。"""
    link = env.root / "escape"
    link.symlink_to("/etc")

    with pytest.raises(SandboxError):
        env.sandbox.resolve_read("escape/passwd")


def test_write_paths_are_relative_to_out_dir(env):
    """写路径一律以 --out-dir 为基准，因此不可能覆盖到原始资料。"""
    target = env.sandbox.resolve_write("notes/alpha.md")

    assert target == env.root / "agent_out" / "notes" / "alpha.md"
    assert target != env.root / "notes" / "alpha.md"


@pytest.mark.parametrize("raw", ["../escape.md", "../../etc/passwd", "/tmp/escape.md"])
def test_write_outside_out_dir_is_rejected(env, raw):
    with pytest.raises(SandboxError):
        env.sandbox.resolve_write(raw)


def test_write_inside_out_dir_is_allowed(env):
    target = env.sandbox.resolve_write("report.md")
    assert target == env.root / "agent_out" / "report.md"


def test_out_dir_must_be_relative_to_root(tmp_path):
    with pytest.raises(SandboxError):
        PathSandbox(tmp_path, "/tmp/absolute-out")
    with pytest.raises(SandboxError):
        PathSandbox(tmp_path, "../escape")
    with pytest.raises(SandboxError):
        PathSandbox(tmp_path / "不存在", "agent_out")


# ---- 命令白名单 --------------------------------------------------------


def test_commands_disabled_by_default():
    allow = CommandAllowlist([])
    assert not allow
    with pytest.raises(SandboxError, match="未启用"):
        allow.resolve("echo")


def test_path_like_command_is_rejected():
    allow = CommandAllowlist(["echo"])
    with pytest.raises(SandboxError, match="裸命令名"):
        allow.resolve("/bin/echo")


def test_unknown_command_is_rejected():
    allow = CommandAllowlist(["echo"])
    with pytest.raises(SandboxError, match="不在白名单"):
        allow.resolve("rm")


def test_whitelisted_command_resolves():
    allow = CommandAllowlist(["echo"])
    assert allow.resolve("echo").endswith("echo")


# ---- 经由 registry 的端到端拒绝 ---------------------------------------


def test_registry_rejects_write_outside_out_dir(env):
    ok, payload = env.registry.dispatch(
        "write_file", {"path": "../pwned.md", "content": "x"}
    )

    assert ok is False
    body = json.loads(payload)
    assert body["ok"] is False
    assert "安全边界" in body["hint"]
    assert not (env.root.parent / "pwned.md").exists()


def test_registry_rejects_disabled_command(env):
    ok, payload = env.registry.dispatch("run_command", {"cmd": "echo", "args": ["hi"]})

    assert ok is False
    assert "未启用" in json.loads(payload)["error"]


def test_registry_runs_whitelisted_command(env):
    ctx = ToolContext(
        sandbox=env.sandbox,
        allowlist=CommandAllowlist(["echo"]),
        dry_run=False,
    )
    registry = build_registry(ctx)

    ok, payload = registry.dispatch("run_command", {"cmd": "echo", "args": ["hello"]})

    assert ok is True
    body = json.loads(payload)
    assert body["ok"] is True
    assert "exit_code=0" in body["result"]
    assert "hello" in body["result"]


def test_dry_run_does_not_touch_disk(env):
    ctx = ToolContext(sandbox=env.sandbox, allowlist=CommandAllowlist([]), dry_run=True)
    registry = build_registry(ctx)

    ok, payload = registry.dispatch(
        "write_file", {"path": "report.md", "content": "不应落盘"}
    )

    assert ok is True
    assert "dry-run" in json.loads(payload)["result"]
    assert not (env.root / "agent_out" / "report.md").exists()
