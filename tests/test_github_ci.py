from pathlib import Path

from guardian.config import Settings
from guardian.github_ci import latest_main_ci


def settings() -> Settings:
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
        allowed_restart_services=("api", "web"),
        expected_services=("db", "api", "web"),
        ci_monitor_enabled=True,
        auto_repair_ci=False,
        auto_repair_files=(),
        request_timeout=5,
        log_tail_lines=80,
        github_token="test-token",
        ai_base_url="",
        ai_model="",
        ai_api_key="",
    )


def test_ci_monitor_returns_failed_job_steps(monkeypatch) -> None:
    def fake_get_json(url: str, token: str):
        if "/actions/runs?" in url:
            return {"workflow_runs": [{
                "id": 17,
                "branch": "main",
                "event": "push",
                "status": "completed",
                "conclusion": "failure",
                "head_sha": "deadbeef",
                "html_url": "https://github.com/aakashsiwach/CEREPATH/actions/runs/17",
            }]}
        return {"jobs": [{
            "id": 101,
            "name": "Python tests",
            "conclusion": "failure",
            "steps": [{"name": "Install", "conclusion": "success", "number": 1},
                      {"name": "Run tests", "conclusion": "failure", "number": 2}],
        }]}

    monkeypatch.setattr("guardian.github_ci._get_json", fake_get_json)
    monkeypatch.setattr("guardian.github_ci._get_failed_job_log", lambda root, job_id, token: "redacted log")
    status = latest_main_ci(settings())
    assert status is not None
    assert status["run_id"] == 17
    assert status["failed_jobs"][0]["failed_steps"][0]["name"] == "Run tests"


def test_failed_job_log_is_redacted_and_download_token_is_not_forwarded(monkeypatch) -> None:
    import io
    import urllib.error
    import zipfile
    from email.message import Message

    from guardian.github_ci import _get_failed_job_log

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("job/log.txt", "Authorization: Bearer very-secret\nGEMINI_API_KEY=hidden-value")

    headers = Message()
    headers["Location"] = "https://logs.example.test/signed/archive.zip?sig=temporary"
    redirect = urllib.error.HTTPError("https://api.github.com/logs", 302, "Found", headers, None)

    class FakeOpener:
        def open(self, request, timeout):
            raise redirect

    captured = {}

    def fake_urlopen(request, timeout):
        captured["headers"] = dict(request.header_items())
        return io.BytesIO(payload.getvalue())

    monkeypatch.setattr("guardian.github_ci.urllib.request.build_opener", lambda *handlers: FakeOpener())
    monkeypatch.setattr("guardian.github_ci.urllib.request.urlopen", fake_urlopen)
    logs = _get_failed_job_log("https://api.github.com/repos/a/b", 11, "github-token")

    assert "very-secret" not in logs
    assert "hidden-value" not in logs
    assert not any(name.lower() == "authorization" for name in captured["headers"])


def test_latest_in_progress_main_run_is_returned_without_querying_old_jobs(monkeypatch) -> None:
    calls = []

    def fake_get_json(url: str, token: str):
        calls.append(url)
        return {"workflow_runs": [{
            "id": 29, "branch": "main", "event": "push", "status": "in_progress",
            "conclusion": None, "head_sha": "cafe1234",
        }]}

    monkeypatch.setattr("guardian.github_ci._get_json", fake_get_json)
    status = latest_main_ci(settings())
    assert status is not None
    assert status["run_id"] == 29
    assert status["status"] == "in_progress"
    assert status["failed_jobs"] == []
    assert len(calls) == 1
