"""配置：命令行参数优先，其次环境变量（含一个极简的 .env 读取器）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

__all__ = ["Config", "load_env_file", "DEFAULT_BASE_URL", "DEFAULT_MODEL"]


def load_env_file(path: Path) -> None:
    """极简 .env 读取器。

    只认 `KEY=VALUE`、忽略空行与 `#` 注释；已有的环境变量优先，不被覆盖。
    刻意不引入 python-dotenv：这点逻辑不值得多一个依赖。
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass
class Config:
    root: Path
    task: str
    out_dir: str = "agent_out"
    max_steps: int = 12
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    allow_cmds: tuple[str, ...] = field(default_factory=tuple)
    dry_run: bool = False
    verbose: bool = True
    timeout: float = 60.0

    @property
    def trace_path(self) -> Path:
        return self.root / self.out_dir / "trace.jsonl"

    @classmethod
    def from_env(cls, **overrides: object) -> "Config":
        """命令行传进来的值优先，其次环境变量，最后内置默认值。"""
        values: dict[str, object] = {
            "model": os.environ.get("AGENT_MODEL", DEFAULT_MODEL),
            "base_url": os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
            "api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
        }
        values.update({key: value for key, value in overrides.items() if value is not None})

        if "root" in values:
            values["root"] = Path(str(values["root"]))
        allow = values.pop("allow_cmds", None)
        values["allow_cmds"] = tuple(allow) if allow else ()  # type: ignore[arg-type]

        return cls(**values)  # type: ignore[arg-type]
