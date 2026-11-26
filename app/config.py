from pathlib import Path
import os

from dotenv import load_dotenv


def positive_setting(name: str, default: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name} 必须是 1 到 {maximum} 的整数。") from None
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} 必须是 1 到 {maximum} 的整数。")
    return value


def load_environment(path: Path | None = None) -> None:
    """Load only this project's .env, preserving explicit deployment settings."""
    env_path = path if path is not None else Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(dotenv_path=env_path, override=False, encoding="utf-8-sig")
