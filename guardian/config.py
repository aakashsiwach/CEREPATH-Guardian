from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE entries without overriding process environment."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    return value in {"1", "true", "yes", "on"}


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


@dataclass(frozen=True)
class Settings:
    target_repo: str
    compose_dir: Path
    compose_file: Path
    base_url: str
    state_dir: Path
    check_interval: int
    failure_threshold: int
    restart_cooldown: int
    auto_restart: bool
    allowed_restart_services: tuple[str, ...]
    expected_services: tuple[str, ...]
    ci_monitor_enabled: bool
    auto_repair_ci: bool
    auto_repair_files: tuple[str, ...]
    request_timeout: int
    log_tail_lines: int
    github_token: str
    ai_base_url: str
    ai_model: str
    ai_api_key: str

    @classmethod
    def load(cls, env_file: Path | None = None) -> "Settings":
        _load_dotenv(env_file or Path(".env"))
        compose_dir = Path(os.getenv("CEREPATH_COMPOSE_DIR", "../CEREPATH")).expanduser().resolve()
        compose_file_value = os.getenv("CEREPATH_COMPOSE_FILE", "docker-compose.yml")
        compose_file = Path(compose_file_value)
        if not compose_file.is_absolute():
            compose_file = compose_dir / compose_file
        compose_file = compose_file.expanduser().resolve()
        if compose_dir != compose_file and compose_dir not in compose_file.parents:
            raise ValueError("CEREPATH_COMPOSE_FILE must stay inside CEREPATH_COMPOSE_DIR")
        base_url = os.getenv("CEREPATH_BASE_URL", "http://127.0.0.1:5173").strip().rstrip("/")
        parsed_base = urlsplit(base_url)
        if (
            parsed_base.scheme not in {"http", "https"}
            or parsed_base.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed_base.username or parsed_base.password or parsed_base.query or parsed_base.fragment
            or parsed_base.path not in {"", "/"}
        ):
            raise ValueError("CEREPATH_BASE_URL must be an HTTP(S) loopback URL without a path, query or credentials")
        services = tuple(
            item.strip() for item in os.getenv("ALLOWED_RESTART_SERVICES", "api,web,browser-agent,egress-proxy").split(",")
            if item.strip()
        )
        # Hard boundary: only known stateless services from the CEREPATH Compose file can restart.
        restartable = {"api", "web", "browser-agent", "egress-proxy"}
        unknown = set(services) - restartable
        if unknown:
            raise ValueError(f"ALLOWED_RESTART_SERVICES contains forbidden/unknown services: {sorted(unknown)}")
        return cls(
            target_repo=os.getenv("CEREPATH_GITHUB_REPO", "aakashsiwach/CEREPATH").strip(),
            compose_dir=compose_dir,
            compose_file=compose_file,
            base_url=base_url,
            state_dir=Path(os.getenv("GUARDIAN_STATE_DIR", "./data")).expanduser().resolve(),
            check_interval=_int("CHECK_INTERVAL_SECONDS", 30, 5, 3600),
            failure_threshold=_int("FAILURES_BEFORE_RESTART", 3, 1, 20),
            restart_cooldown=_int("RESTART_COOLDOWN_SECONDS", 600, 60, 86400),
            auto_restart=_bool("AUTO_RESTART_SERVICES", False),
            allowed_restart_services=services,
            expected_services=tuple(item.strip() for item in os.getenv("EXPECTED_COMPOSE_SERVICES", "db,egress-proxy,browser-agent,api,web").split(",") if item.strip()),
            ci_monitor_enabled=_bool("CI_MONITOR_ENABLED", False),
            auto_repair_ci=_bool("AUTO_REPAIR_CI", False),
            auto_repair_files=tuple(item.strip() for item in os.getenv("AUTO_REPAIR_FILES", "").split(",") if item.strip()),
            request_timeout=_int("REQUEST_TIMEOUT_SECONDS", 5, 1, 30),
            log_tail_lines=_int("LOG_TAIL_LINES", 100, 20, 500),
            github_token=os.getenv("GITHUB_TOKEN", "").strip(),
            ai_base_url=os.getenv("AI_BASE_URL", "").strip().rstrip("/"),
            ai_model=os.getenv("AI_MODEL", "").strip(),
            ai_api_key=os.getenv("AI_API_KEY", "").strip(),
        )
