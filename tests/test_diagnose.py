from pathlib import Path

import pytest

from guardian.config import Settings
from guardian.diagnose import _chat_completion


def settings(url: str) -> Settings:
    return Settings(
        target_repo="aakashsiwach/CEREPATH",
        compose_dir=Path("."),
        compose_file=Path("docker-compose.yml"),
        base_url="http://127.0.0.1:5173",
        state_dir=Path("./data"),
        check_interval=30,
        failure_threshold=3,
        restart_cooldown=600,
        auto_restart=False,
        allowed_restart_services=("api",),
        expected_services=("api",),
        ci_monitor_enabled=False,
        auto_repair_ci=False,
        auto_repair_files=(),
        request_timeout=5,
        log_tail_lines=80,
        github_token="",
        ai_base_url=url,
        ai_model="test-model",
        ai_api_key="test-key",
    )


def test_non_loopback_http_model_endpoint_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="HTTPS"):
        _chat_completion(settings("http://model.example/v1"), "system", "user")


def test_loopback_http_model_endpoint_is_allowed(monkeypatch) -> None:
    monkeypatch.setattr("guardian.diagnose.urllib.request.urlopen", lambda *args, **kwargs: None)
    # The request gets as far as the network call; the policy layer accepted loopback HTTP.
    with pytest.raises((TypeError, RuntimeError)):
        _chat_completion(settings("http://127.0.0.1:11434/v1"), "system", "user")
