from dataclasses import replace
from pathlib import Path

from guardian.config import Settings
from guardian.monitor import Guardian


def settings(tmp_path: Path) -> Settings:
    return Settings(
        target_repo="aakashsiwach/CEREPATH",
        compose_dir=tmp_path,
        compose_file=tmp_path / "docker-compose.yml",
        base_url="http://127.0.0.1:5173",
        state_dir=tmp_path / "data",
        check_interval=30,
        failure_threshold=3,
        restart_cooldown=600,
        auto_restart=False,
        allowed_restart_services=("api", "web"),
        expected_services=("db", "api", "web"),
        ci_monitor_enabled=False,
        auto_repair_ci=False,
        auto_repair_files=(),
        request_timeout=2,
        log_tail_lines=80,
        github_token="",
        ai_base_url="",
        ai_model="",
        ai_api_key="",
    )


def set_healthy_service_probes(guardian: Guardian) -> None:
    guardian._probe_service = lambda service: {"ok": True, "source": "test"}  # type: ignore[method-assign]


def test_compose_parser_supports_array_and_json_lines() -> None:
    array = '[{"Service":"api","State":"running"}]'
    lines = '{"Service":"api","State":"running"}\n{"Service":"web","State":"running"}'
    assert len(Guardian._parse_compose_json(array)) == 1
    assert len(Guardian._parse_compose_json(lines)) == 2


def test_database_restart_is_rejected(tmp_path: Path) -> None:
    guardian = Guardian(settings(tmp_path))
    result = guardian._restart_allowed_service("db")
    assert result["attempted"] is False
    assert result["reason"] == "service_not_allowlisted"


def test_check_reports_missing_expected_service_and_redacts_logs(tmp_path: Path) -> None:
    guardian = Guardian(replace(settings(tmp_path), expected_services=("db", "api", "web", "browser-agent")))
    set_healthy_service_probes(guardian)
    guardian._health_probe = lambda: {"ok": True, "http_status": 200}  # type: ignore[method-assign]
    guardian._compose_services = lambda: {  # type: ignore[method-assign]
        "db": {"name": "db", "state": "running", "health": "healthy"},
        "api": {"name": "api", "state": "running", "health": "unknown"},
        "web": {"name": "web", "state": "running", "health": "unknown"},
    }
    guardian._collect_logs = lambda: "Authorization: Bearer secret-value"  # type: ignore[method-assign]
    report = guardian.check_once()
    assert report["healthy"] is False
    assert report["compose"]["missing_services"] == ["browser-agent"]
    assert "secret-value" not in report["diagnostic_logs_redacted"]


def test_database_service_cannot_be_added_to_restart_allowlist(monkeypatch, tmp_path: Path) -> None:
    from guardian.config import Settings
    import pytest

    env_file = tmp_path / ".env"
    env_file.write_text("ALLOWED_RESTART_SERVICES=api,db\n", encoding="utf-8")
    monkeypatch.setenv("ALLOWED_RESTART_SERVICES", "api,db")
    with pytest.raises(ValueError):
        Settings.load(env_file)


def test_restarts_only_exited_allowlisted_service_after_threshold(tmp_path: Path) -> None:
    import subprocess

    guardian = Guardian(replace(settings(tmp_path), auto_restart=True, failure_threshold=1))
    set_healthy_service_probes(guardian)
    guardian._health_probe = lambda: {"ok": False, "error": "timeout"}  # type: ignore[method-assign]
    guardian._compose_services = lambda: {  # type: ignore[method-assign]
        "db": {"name": "db", "state": "running", "health": "healthy"},
        "api": {"name": "api", "state": "exited", "health": "unknown"},
        "web": {"name": "web", "state": "running", "health": "unknown"},
    }
    calls: list[tuple[str, ...]] = []

    def fake_compose(*args: str, **kwargs):
        calls.append(tuple(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    guardian._compose = fake_compose  # type: ignore[method-assign]
    guardian._collect_logs = lambda: "api exited"  # type: ignore[method-assign]
    report = guardian.check_once()
    assert calls == [("restart", "api")]
    assert report["actions"][0]["command_succeeded"] is True
    assert report["actions"][0]["health_verification"] == "pending_next_check"


def test_missing_service_uses_no_deps_existing_image_recovery(tmp_path: Path) -> None:
    import subprocess

    guardian = Guardian(replace(settings(tmp_path), auto_restart=True, failure_threshold=1))
    set_healthy_service_probes(guardian)
    guardian._health_probe = lambda: {"ok": False, "error": "timeout"}  # type: ignore[method-assign]
    guardian._compose_services = lambda: {  # type: ignore[method-assign]
        "db": {"name": "db", "state": "running", "health": "healthy"},
        "api": {"name": "api", "state": "running", "health": "unknown"},
    }
    calls: list[tuple[str, ...]] = []

    def fake_compose(*args: str, **kwargs):
        calls.append(tuple(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    guardian._compose = fake_compose  # type: ignore[method-assign]
    guardian._collect_logs = lambda: "web missing"  # type: ignore[method-assign]
    report = guardian.check_once()
    assert calls == [("up", "-d", "--no-deps", "--no-build", "--pull", "never", "web")]
    assert report["actions"][0]["action"] == "start_existing_image"


def test_http_failure_alone_does_not_restart_running_services(tmp_path: Path) -> None:
    import subprocess

    guardian = Guardian(replace(settings(tmp_path), auto_restart=True, failure_threshold=1))
    set_healthy_service_probes(guardian)
    guardian._health_probe = lambda: {"ok": False, "error": "timeout"}  # type: ignore[method-assign]
    guardian._compose_services = lambda: {  # type: ignore[method-assign]
        "db": {"name": "db", "state": "running", "health": "healthy"},
        "api": {"name": "api", "state": "running", "health": "unknown"},
        "web": {"name": "web", "state": "running", "health": "unknown"},
    }
    calls: list[tuple[str, ...]] = []

    def fake_compose(*args: str, **kwargs):
        calls.append(tuple(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    guardian._compose = fake_compose  # type: ignore[method-assign]
    guardian._collect_logs = lambda: "health probe failed"  # type: ignore[method-assign]
    guardian.check_once()
    assert calls == []


def test_running_ci_does_not_hide_behind_older_success_or_mark_runtime_unhealthy(tmp_path: Path, monkeypatch) -> None:
    guardian = Guardian(replace(settings(tmp_path), ci_monitor_enabled=True))
    set_healthy_service_probes(guardian)
    guardian._health_probe = lambda: {"ok": True, "http_status": 200}  # type: ignore[method-assign]
    guardian._compose_services = lambda: {  # type: ignore[method-assign]
        name: {"name": name, "state": "running", "health": "healthy" if name == "db" else "unknown"}
        for name in ("db", "api", "web")
    }
    monkeypatch.setattr("guardian.monitor.latest_main_ci", lambda config: {
        "enabled": True, "available": True, "status": "in_progress", "conclusion": None, "run_id": 23,
    })
    report = guardian.check_once()
    assert report["code_health"]["ci_state"] == "running"
    assert report["overall_healthy"] is True


def test_cancelled_ci_is_not_reported_as_passed(tmp_path: Path, monkeypatch) -> None:
    guardian = Guardian(replace(settings(tmp_path), ci_monitor_enabled=True))
    set_healthy_service_probes(guardian)
    guardian._health_probe = lambda: {"ok": True, "http_status": 200}  # type: ignore[method-assign]
    guardian._compose_services = lambda: {  # type: ignore[method-assign]
        name: {"name": name, "state": "running", "health": "healthy" if name == "db" else "unknown"}
        for name in ("db", "api", "web")
    }
    monkeypatch.setattr("guardian.monitor.latest_main_ci", lambda config: {
        "enabled": True, "available": True, "status": "completed", "conclusion": "cancelled", "run_id": 24,
    })
    report = guardian.check_once()
    assert report["code_health"]["ci_state"] == "unknown"
    assert report["overall_healthy"] is False


def test_repeated_internal_api_probe_failures_can_restart_api(tmp_path: Path) -> None:
    import subprocess

    guardian = Guardian(replace(settings(tmp_path), auto_restart=True, failure_threshold=2))
    guardian._health_probe = lambda: {"ok": False, "error": "proxy unavailable"}  # type: ignore[method-assign]
    guardian._compose_services = lambda: {  # type: ignore[method-assign]
        "db": {"name": "db", "state": "running", "health": "healthy"},
        "api": {"name": "api", "state": "running", "health": "unknown"},
        "web": {"name": "web", "state": "running", "health": "unknown"},
    }
    guardian._probe_service = lambda name: {"ok": name != "api", "source": "test"}  # type: ignore[method-assign]
    calls: list[tuple[str, ...]] = []

    def fake_compose(*args: str, **kwargs):
        calls.append(tuple(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    guardian._compose = fake_compose  # type: ignore[method-assign]
    guardian._collect_logs = lambda: "api liveness failed"  # type: ignore[method-assign]
    guardian.check_once()
    report = guardian.check_once()
    assert calls == [("restart", "api")]
    assert report["compose"]["unhealthy_services"] == ["api"]


def test_fixed_container_probes_do_not_use_shell_or_user_controlled_commands(tmp_path: Path) -> None:
    import subprocess

    guardian = Guardian(settings(tmp_path))
    calls: list[tuple[str, ...]] = []

    def fake_compose(*args: str, **kwargs):
        calls.append(tuple(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    guardian._compose = fake_compose  # type: ignore[method-assign]
    assert guardian._probe_service("api")["ok"] is True
    assert guardian._probe_service("web")["ok"] is True
    assert guardian._probe_service("db") is None
    assert calls[0][:5] == ("exec", "-T", "api", "python", "-c")
    assert calls[0][-1].count("json.load(r)") == 1
    assert "cerepath-api" in calls[0][-1]
    assert calls[1] == ("exec", "-T", "web", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1/api/health")
    assert all("sh" not in call for call in calls)


def test_failed_restart_attempt_enters_cooldown(tmp_path: Path) -> None:
    import subprocess

    guardian = Guardian(replace(settings(tmp_path), auto_restart=True, failure_threshold=1))
    guardian._health_probe = lambda: {"ok": False, "error": "down"}  # type: ignore[method-assign]
    guardian._compose_services = lambda: {  # type: ignore[method-assign]
        "db": {"name": "db", "state": "running", "health": "healthy"},
        "api": {"name": "api", "state": "exited", "health": "unknown"},
        "web": {"name": "web", "state": "running", "health": "unknown"},
    }
    set_healthy_service_probes(guardian)
    calls = []

    def failed_compose(*args: str, **kwargs):
        calls.append(tuple(args))
        return subprocess.CompletedProcess(args, 1, "", "restart failed")

    guardian._compose = failed_compose  # type: ignore[method-assign]
    guardian._collect_logs = lambda: "api exited"  # type: ignore[method-assign]
    first = guardian.check_once()
    second = guardian.check_once()
    assert first["actions"][0]["command_succeeded"] is False
    assert second["actions"][0]["reason"] == "restart_cooldown_active"
    assert calls == [("restart", "api")]


def test_ci_repair_refuses_to_patch_without_failed_job_logs(tmp_path: Path, monkeypatch) -> None:
    guardian = Guardian(replace(
        settings(tmp_path),
        ci_monitor_enabled=True,
        auto_repair_ci=True,
        auto_repair_files=("backend/app/example.py",),
        ai_base_url="https://model.example/v1",
        ai_model="test-model",
        github_token="test-token",
    ))
    set_healthy_service_probes(guardian)
    guardian._health_probe = lambda: {"ok": True, "http_status": 200}  # type: ignore[method-assign]
    guardian._compose_services = lambda: {  # type: ignore[method-assign]
        name: {"name": name, "state": "running", "health": "healthy" if name == "db" else "unknown"}
        for name in ("db", "api", "web")
    }
    monkeypatch.setattr("guardian.monitor.latest_main_ci", lambda config: {
        "enabled": True, "available": True, "status": "completed", "conclusion": "failure",
        "run_id": 25, "failed_jobs": [{"id": 1, "name": "tests", "redacted_log_excerpt": "", "log_error": "log unavailable"}],
    })

    def unexpected_repair(*args, **kwargs):
        raise AssertionError("Repair must not run without failed-job logs")

    monkeypatch.setattr("guardian.monitor.propose_repair", unexpected_repair)
    report = guardian.check_once()
    repair_action = next(action for action in report["actions"] if action.get("type") == "ci_repair")
    assert repair_action["attempted"] is False
    assert repair_action["reason"] == "failing_job_logs_unavailable"


def test_target_health_url_must_be_loopback(monkeypatch, tmp_path: Path) -> None:
    import pytest

    env_file = tmp_path / ".env"
    env_file.write_text("CEREPATH_BASE_URL=https://example.com\n", encoding="utf-8")
    monkeypatch.setenv("CEREPATH_BASE_URL", "https://example.com")
    with pytest.raises(ValueError, match="loopback"):
        Settings.load(env_file)


def test_compose_file_cannot_escape_target_checkout(monkeypatch, tmp_path: Path) -> None:
    import pytest

    target = tmp_path / "CEREPATH"
    target.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text(f"CEREPATH_COMPOSE_DIR={target}\nCEREPATH_COMPOSE_FILE=../other.yml\n", encoding="utf-8")
    monkeypatch.delenv("CEREPATH_COMPOSE_DIR", raising=False)
    monkeypatch.setenv("CEREPATH_COMPOSE_FILE", "../other.yml")
    with pytest.raises(ValueError, match="inside CEREPATH_COMPOSE_DIR"):
        Settings.load(env_file)


def test_health_probe_only_records_expected_fields(monkeypatch, tmp_path: Path) -> None:
    import io
    import json

    guardian = Guardian(settings(tmp_path))

    class Response(io.BytesIO):
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()

    payload = json.dumps({
        "status": "ok", "service": "cerepath-api", "version": "0.4.0", "secret": "should-not-be-recorded",
    }).encode()
    monkeypatch.setattr("guardian.monitor.urllib.request.urlopen", lambda *args, **kwargs: Response(payload))
    result = guardian._health_probe()
    assert result["ok"] is True
    assert result["body"] == {"status": "ok", "service": "cerepath-api", "version": "0.4.0"}
    assert "secret" not in result["body"]
