"""安全边界。

这个文件回答一个绕不开的问题：**凭什么让一个 LLM 去动你的文件系统？**

两条边界：
  1. 路径沙箱——所有文件读写都必须落在 --root 之内（且写操作进一步限制在 --out-dir）；
     用 resolve() 展开符号链接后再判断，挡掉 ../ 逃逸和软链接逃逸。
  2. 命令白名单——--allow-cmd 里没有的命令一律拒绝，且用 shell=False 执行，
     管道、重定向、`&&` 都不会被解释。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Iterable

__all__ = ["SandboxError", "PathSandbox", "CommandAllowlist"]


class SandboxError(Exception):
    """越界访问被拒绝。调用方会把它转成结构化错误回灌给模型。"""


class PathSandbox:
    """把路径参数约束在 root 之内，写操作再额外约束在 out_dir 之内。"""

    def __init__(self, root: Path, out_dir: str = "agent_out") -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise SandboxError(f"--root 不是一个存在的目录: {root}")

        out_path = Path(out_dir)
        if out_path.is_absolute():
            raise SandboxError(f"--out-dir 必须是相对 --root 的路径: {out_dir}")
        # out_dir 可能还不存在，resolve(strict=False) 不会报错
        self.out_dir = (self.root / out_path).resolve()
        if not self.out_dir.is_relative_to(self.root):
            raise SandboxError(f"--out-dir 逃出了 --root: {out_dir}")

    # ---- 对外接口 ------------------------------------------------------

    def resolve_read(self, raw: str) -> Path:
        target = self._resolve_in(raw, self.root)
        self._require_within(target, self.root, raw, "--root")
        return target

    def resolve_write(self, raw: str) -> Path:
        # 写路径相对 --out-dir 解析：模型写 "report.md" 就该落在 agent_out/report.md
        target = self._resolve_in(raw, self.out_dir)
        self._require_within(target, self.out_dir, raw, "--out-dir")
        return target

    def rel(self, path: Path) -> str:
        """转成相对 root 的展示用路径，trace 里更好读。"""
        try:
            return str(path.relative_to(self.root)) or "."
        except ValueError:
            return str(path)

    # ---- 内部 ----------------------------------------------------------

    def _resolve_in(self, raw: str, base: Path) -> Path:
        if not isinstance(raw, str) or not raw.strip():
            raise SandboxError("路径不能为空")
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = base / candidate
        # resolve() 展开符号链接：软链接指到 root 外时也会在这里被抓住
        return candidate.resolve()

    @staticmethod
    def _require_within(target: Path, base: Path, raw: str, scope: str) -> None:
        if target.is_relative_to(base):
            return
        raise SandboxError(f"拒绝访问 {raw!r}：它不在 {scope} 范围内")


class CommandAllowlist:
    """只放行白名单里的裸命令名，强制 shell=False 执行。"""

    def __init__(self, allowed: Iterable[str]) -> None:
        self.allowed = {name.strip() for name in allowed if name.strip()}

    def __bool__(self) -> bool:
        return bool(self.allowed)

    def resolve(self, cmd: str) -> str:
        if not self.allowed:
            raise SandboxError("命令执行未启用（用 --allow-cmd 显式开启白名单）")
        if not isinstance(cmd, str) or not cmd.strip():
            raise SandboxError("命令不能为空")
        if "/" in cmd or "\\" in cmd:
            raise SandboxError(f"只接受裸命令名，不接受路径: {cmd!r}")
        if cmd not in self.allowed:
            raise SandboxError(
                f"命令 {cmd!r} 不在白名单内，当前允许: {sorted(self.allowed)}"
            )
        resolved = shutil.which(cmd)
        if resolved is None:
            raise SandboxError(f"白名单命令 {cmd!r} 在本机不存在")
        return resolved
