from __future__ import annotations

import io
import json
import zipfile
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from .redaction import redact

from .config import Settings


class GitHubCIError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _get_json(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "CEREPATH-Guardian/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read(2_000_000))
    except urllib.error.HTTPError as exc:
        raise GitHubCIError(f"GitHub Actions API returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise GitHubCIError(f"GitHub Actions API request failed: {type(exc).__name__}") from exc


def _extract_log_archive(payload: bytes) -> str:
    output: list[str] = []
    total_chars = 0
    total_uncompressed = 0
    entries_seen = 0
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (zipfile.BadZipFile, OSError) as exc:
        raise GitHubCIError("GitHub job log response was not a valid ZIP archive") from exc
    with archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            entries_seen += 1
            if entries_seen > 50 or total_chars >= 40_000 or total_uncompressed >= 4_000_000:
                break
            if entry.file_size > 2_000_000:
                continue
            try:
                raw = archive.read(entry)
            except (OSError, RuntimeError, zipfile.BadZipFile):
                continue
            total_uncompressed += len(raw)
            if total_uncompressed > 4_000_000:
                raw = raw[: max(0, 4_000_000 - (total_uncompressed - len(raw)))]
            text = redact(raw.decode("utf-8", errors="replace"))
            remaining = 40_000 - total_chars
            text = text[-remaining:]
            output.append(redact(f"--- {entry.filename} ---\n{text}"))
            total_chars += len(text)
    return "\n".join(output)


def _get_failed_job_log(root: str, job_id: int, token: str) -> str:
    """Download a job log ZIP without forwarding the GitHub token to its signed URL."""
    request = urllib.request.Request(
        f"{root}/actions/jobs/{job_id}/logs",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "CEREPATH-Guardian/0.1",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            archive_bytes = response.read(8_000_001)
    except urllib.error.HTTPError as exc:
        if exc.code not in {301, 302, 303, 307, 308}:
            raise GitHubCIError(f"GitHub job log endpoint returned HTTP {exc.code}") from exc
        location = exc.headers.get("Location", "")
        parsed = urlsplit(location)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise GitHubCIError("GitHub returned an unsafe job-log download URL") from exc
        # Intentionally omit Authorization on this second request. The URL itself is short-lived.
        try:
            with urllib.request.urlopen(urllib.request.Request(location, headers={"User-Agent": "CEREPATH-Guardian/0.1"}), timeout=30) as response:
                archive_bytes = response.read(8_000_001)
        except (urllib.error.URLError, TimeoutError, OSError) as download_error:
            raise GitHubCIError(f"Signed GitHub job-log download failed: {type(download_error).__name__}") from download_error
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GitHubCIError(f"GitHub job-log request failed: {type(exc).__name__}") from exc
    if len(archive_bytes) > 8_000_000:
        raise GitHubCIError("GitHub job-log archive exceeded the 8 MB safety limit")
    return _extract_log_archive(archive_bytes)


def latest_main_ci(settings: Settings) -> dict[str, Any] | None:
    """Read the latest completed push workflow run on main and summarize failed steps."""
    if not settings.ci_monitor_enabled:
        return None
    if not settings.github_token:
        return {"enabled": True, "available": False, "reason": "github_token_not_configured"}
    parts = settings.target_repo.split("/")
    if len(parts) != 2 or not all(parts) or any(not item.replace("-", "").replace("_", "").replace(".", "").isalnum() for item in parts):
        raise GitHubCIError("CEREPATH_GITHUB_REPO must be a valid owner/repository pair")
    owner, repository = parts
    root = f"https://api.github.com/repos/{owner}/{repository}"
    runs = _get_json(f"{root}/actions/runs?branch=main&event=push&per_page=10", settings.github_token)
    candidates = [
        item for item in runs.get("workflow_runs", [])
        if item.get("branch") == "main" and item.get("event") == "push"
    ]
    if not candidates:
        return {"enabled": True, "available": False, "reason": "no_main_push_run"}
    # GitHub returns runs newest-first. Observe the current run instead of hiding it
    # behind an older completed green run while the newest workflow is still executing.
    run = candidates[0]
    run_id = int(run["id"])
    base_status = {
        "enabled": True,
        "available": True,
        "run_id": run_id,
        "workflow": run.get("name"),
        "run_number": run.get("run_number"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "head_sha": run.get("head_sha"),
        "html_url": run.get("html_url"),
        "created_at": run.get("created_at"),
    }
    if run.get("status") != "completed":
        base_status["failed_jobs"] = []
        return base_status
    jobs_payload = _get_json(f"{root}/actions/runs/{run_id}/jobs?per_page=100", settings.github_token)
    failed_jobs: list[dict[str, Any]] = []
    for job in jobs_payload.get("jobs", []):
        if job.get("conclusion") != "failure":
            continue
        failed_steps = [
            {"name": step.get("name"), "conclusion": step.get("conclusion"), "number": step.get("number")}
            for step in job.get("steps", []) if step.get("conclusion") == "failure"
        ]
        job_id = int(job["id"])
        try:
            log_excerpt = _get_failed_job_log(root, job_id, settings.github_token)
            log_error = None
        except GitHubCIError as exc:
            log_excerpt = ""
            log_error = str(exc)
        failed_jobs.append({"id": job_id, "name": job.get("name"), "failed_steps": failed_steps, "redacted_log_excerpt": log_excerpt, "log_error": log_error})
    base_status["failed_jobs"] = failed_jobs
    return base_status
