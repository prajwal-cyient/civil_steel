"""Local runtime configuration. Secrets are never returned to the browser."""

from dataclasses import dataclass
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def workflow_mode() -> str:
    value = os.getenv("PLANT_WORKFLOW_MODE", "poc2_automation")
    if value not in ("poc1_automation", "poc2_automation", "reviewed_engineering"):
        raise ValueError(f"Unsupported workflow mode: {value}")
    return value


def load_environment(path: Path | None = None) -> None:
    """Load simple KEY=value settings without executing shell code or interpolation."""
    source = path or ROOT / ".env"
    if not source.is_file():
        return
    for raw in source.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not key.isidentifier():
            raise ValueError("Invalid .env setting; expected KEY=value.")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _integer(name: str, default: int, minimum: int = 1) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}.")
    return value


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    run_timeout: int
    agent_timeout: int
    frame_revisions: int
    connection_revisions: int
    model_call_limit: int
    model: str
    live_enabled: bool
    llm_enabled: bool
    templates: Path


def settings() -> Settings:
    return Settings(
        host=os.getenv("PLANT_HOST", "127.0.0.1"), port=_integer("PLANT_PORT", 8080),
        run_timeout=_integer("PLANT_RUN_TIMEOUT_SECONDS", 3600),
        agent_timeout=_integer("PLANT_AGENT_TIMEOUT_SECONDS", 180),
        frame_revisions=_integer("PLANT_MAX_FRAME_REVISIONS", 3, 0),
        connection_revisions=_integer("PLANT_MAX_CONNECTION_REVISIONS", 3, 0),
        model_call_limit=_integer("PLANT_MODEL_CALL_LIMIT", 6, 0),
        model=os.getenv("PLANT_AGENT_MODEL", ""),
        live_enabled=os.getenv("PLANT_LIVE_ENGINEERING_ENABLED", "true").lower() == "true",
        llm_enabled=os.getenv("PLANT_LLM_ENABLED", "true").lower() == "true",
        templates=Path(os.getenv("PLANT_TEMPLATE_DIRECTORY", str(ROOT / "sample_projects"))).resolve(),
    )
