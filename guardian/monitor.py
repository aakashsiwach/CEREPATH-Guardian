from __future__ import annotations

import json
import logging
import subprocess
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .github_ci import GitHubCIError, latest_main_ci
from .repair import RepairError, propose_repair
from .redaction import redact
from .store import EventStore

LOG = logging.getLogger("cerepath_guardian")


class CommandError(RuntimeError):
    pass


class Guardian:
    """Observe CEREPATH and optionally restart only allowlisted, stateless services."""

    def __init__(self, settings: Settings, store: EventStore | None = None) -> None:
        self.settings = settings
        self.store = store or EventStore(settings.state_dir / "guardian.sqlite3")
        self.reports_dir = settings.state_dir / "reports"
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self._prune_old_reports()

    def _prune_old_reports(self) -> None:
        cutoff = datetime.now(timezone.utc).timestamp() - (30 * 24 * 60 * 60)
        for path in self.reports_dir.glob("incident-*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    def _compose(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        if not self.settings.compose_dir.is_dir():
            raise CommandError(f"CEREPATH compose directory does not exist: {self.settings.compose_dir}")
        if not self.settings.compose_file.is_file():
            raise CommandError(f"Compose file does not exist: {self.settings.compose_file}")
        command = ["docker", "compose", "-f", str(self.settings.compose_file), *args]
        try:
            return subprocess.run(
                command,
                cwd=self.settings.compose_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CommandError(f"Docker Compose command failed: {type(exc).__name__}") from exc

    def _health_probe(self) -> dict[str, Any]:
        url = f"{self.settings.base_url}/api/health"
        request = urllib.request.Request(url, headers={"User-Agent": "CEREPATH-Guardian/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=self.settings.request_timeout) as response:
                payload = response.read(16_384).decode("utf-8", errors="replace")
                body = json.loads(payload)
                if not isinstance(body, dict):
                    return {"ok": False, "http_status": response.status, "error": "Unexpected health response shape"}
                ok = response.status == 200 and body.get("status") == "ok" and body.get("service") == "cerepath-api"
                safe_body = {key: body.get(key) for key in ("status", "service", "version") if key in body}
                return {"ok": ok, "http_status": response.status, "body": safe_body}
        except urllib.error.HTTPError as exc:
            return {"ok": False, "http_status": exc.code, "error": f"HTTP {exc.code}"}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return {"ok": False, "error": type(exc).__name__}

    @staticmethod
    def _parse_compose_json(output: str) -> list[dict[str, Any]]:
        text = output.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            records: list[dict[str, Any]] = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        records.append(item)
                except json.JSONDecodeError:
                    continue
            return records

    def _compose_services(self) -> dict[str, Any]:
        result = self._compose("ps", "--all", "--format", "json")
        if result.returncode != 0:
            raise CommandError(redact(result.stderr or result.stdout or "docker compose ps failed")[:1000])
        services: dict[str, Any] = {}
        for item in self._parse_compose_json(result.stdout):
            name = str(item.get("Service") or item.get("service") or item.get("Name") or item.get("name") or "unknown")
            services[name] = {
                "name": name,
                "state": str(item.get("State") or item.get("state") or "unknown").lower(),
                "health": str(item.get("Health") or item.get("health") or "unknown").lower(),
                "exit_code": item.get("ExitCode") or item.get("exit_code"),
            }
        return services

    def _probe_service(self, service: str) -> dict[str, Any] | None:
        """Run fixed, read-only liveness checks inside CEREPATH's API and web containers."""
        if service == "api":
            code = (
                "import json,urllib.request; "
                "r=urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=3); "
                "d=json.load(r); "
                "raise SystemExit(0 if r.status==200 and d.get('status')=='ok' "
                "and d.get('service')=='cerepath-api' else 1)"
            )
            command = ("exec", "-T", "api", "python", "-c", code)
        elif service == "web":
            # This exercises Nginx itself and its /api proxy route, not just its process state.
            command = ("exec", "-T", "web", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1/api/health")
        else:
            return None  # Other services already expose Docker healthchecks.
        try:
            result = self._compose(*command, timeout=12)
        except CommandError as exc:
            return {"ok": False, "source": "container_exec", "error": str(exc)[:500]}
        return {
            "ok": result.returncode == 0,
            "source": "container_exec",
            "error": None if result.returncode == 0 else redact(result.stderr or result.stdout or "service probe failed")[:500],
        }

    def _collect_logs(self) -> str:
        result = self._compose(
            "logs", "--no-color", "--tail", str(self.settings.log_tail_lines), "--since", "10m",
            "api", "web", "browser-agent", "egress-proxy", timeout=45,
        )
        text = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return redact(text)[-50_000:]

    def _restart_allowed_service(self, service: str, *, missing: bool = False) -> dict[str, Any]:
        if service not in self.settings.allowed_restart_services:
            return {"attempted": False, "reason": "service_not_allowlisted"}
        if service.lower() in {"db", "postgres", "postgresql", "database"}:
            return {"attempted": False, "reason": "stateful_service_restart_forbidden"}
        previous = self.store.last_restart(service)
        now = datetime.now(timezone.utc)
        if previous and (now - previous).total_seconds() < self.settings.restart_cooldown:
            return {"attempted": False, "reason": "restart_cooldown_active"}
        # Missing services may only start from a local image: forbid both image pulls and builds.
        command = ("up", "-d", "--no-deps", "--no-build", "--pull", "never", service) if missing else ("restart", service)
        try:
            result = self._compose(*command, timeout=90)
        except CommandError as exc:
            self.store.record_restart(service, reset_failures=False)
            return {"attempted": True, "command_succeeded": False, "service": service, "error": redact(str(exc))[:1000]}
        if result.returncode == 0:
            self.store.record_restart(service, reset_failures=True)
            return {
                "attempted": True,
                "command_succeeded": True,
                "service": service,
                "action": "start_existing_image" if missing else "restart",
                "health_verification": "pending_next_check",
            }
        self.store.record_restart(service, reset_failures=False)
        return {"attempted": True, "command_succeeded": False, "service": service, "error": redact(result.stderr or result.stdout or "recovery command failed")[:1000]}

    def check_once(self) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        health = self._health_probe()
        compose_error = None
        try:
            services = self._compose_services()
        except CommandError as exc:
            services = {}
            compose_error = str(exc)
        ci_status: dict[str, Any] | None = None
        ci_error: str | None = None
        try:
            ci_status = latest_main_ci(self.settings)
        except GitHubCIError as exc:
            ci_error = str(exc)

        # Host HTTP failures alone never trigger restarts. Docker must confirm a stopped,
        # unhealthy or missing service, or a fixed in-container API/Nginx probe must fail.
        missing_services = sorted(set(self.settings.expected_services) - set(services)) if compose_error is None else []
        candidates: list[tuple[str, bool]] = []
        for name, status in services.items():
            if status["state"] == "running" and name in {"api", "web"}:
                status["probe"] = self._probe_service(name)
            probe = status.get("probe")
            if probe is not None:
                raw_probe_ok = bool(probe.get("ok"))
                # A successful host probe traverses Nginx and FastAPI end to end.
                # If docker exec is unavailable/transiently failing while that route
                # succeeds, retain the raw failure for diagnostics but do not classify
                # a healthy API/web service as down or restart it.
                corroborated = not raw_probe_ok and bool(health.get("ok")) and name in {"api", "web"}
                probe["effective_ok"] = raw_probe_ok or corroborated
                if corroborated:
                    probe["corroborated_by_host_health"] = True
            probe_failed = bool(probe is not None and not probe.get("effective_ok", probe.get("ok")))
            failed = status["state"] in {"exited", "dead"} or status["health"] == "unhealthy" or probe_failed
            count = self.store.failures(name) + 1 if failed else 0
            self.store.set_failures(name, count)
            restartable_failure = status["state"] in {"exited", "dead"} or status["health"] == "unhealthy" or probe_failed
            if failed and count >= self.settings.failure_threshold and restartable_failure:
                candidates.append((name, False))
        for name in missing_services:
            count = self.store.failures(name) + 1
            self.store.set_failures(name, count)
            if count >= self.settings.failure_threshold:
                candidates.append((name, True))

        actions: list[dict[str, Any]] = []
        if self.settings.auto_restart:
            for service, missing in candidates:
                actions.append(self._restart_allowed_service(service, missing=missing))
        elif candidates:
            actions.append({"attempted": False, "reason": "automatic_restart_disabled", "services": [name for name, _ in candidates]})

        compose_ok = compose_error is None and bool(services) and not missing_services
        bad_services = [
            name for name, status in services.items()
            if status["state"] not in {"running"} or status["health"] == "unhealthy"
            or (status.get("probe") is not None and not status["probe"].get("effective_ok", status["probe"].get("ok")))
        ]
        healthy = bool(health.get("ok")) and compose_ok and not bad_services
        logs = ""
        if not self.settings.ci_monitor_enabled:
            ci_state = "not_monitored"
        elif ci_error:
            ci_state = "error"
        elif not ci_status or not ci_status.get("available"):
            ci_state = "unavailable"
        elif ci_status.get("status") != "completed":
            ci_state = "running"
        elif ci_status.get("conclusion") == "success":
            ci_state = "passed"
        elif ci_status.get("conclusion") == "failure":
            ci_state = "failed"
        else:
            # Cancelled, timed-out, action-required, neutral and unknown outcomes
            # must never be reported as a green code-health result.
            ci_state = "unknown"
        report: dict[str, Any] = {
            "schema_version": 1,
            "checked_at": checked_at,
            "target_repo": self.settings.target_repo,
            "health_probe": health,
            "compose": {"ok": compose_ok, "error": compose_error, "services": services, "missing_services": missing_services, "unhealthy_services": bad_services},
            "actions": actions,
            "github_ci": ci_status,
            "github_ci_error": ci_error,
            "healthy": healthy,
            "code_health": {
                "ci_monitor_enabled": self.settings.ci_monitor_enabled,
                "ci_state": ci_state,
                "latest_ci_conclusion": ci_status.get("conclusion") if ci_status else None,
                "latest_ci_failed": bool(ci_status and ci_status.get("conclusion") == "failure"),
            },
            "overall_healthy": healthy and ci_state in {"not_monitored", "passed", "running"},
            "policy": {
                "auto_restart_enabled": self.settings.auto_restart,
                "allowed_restart_services": list(self.settings.allowed_restart_services),
                "expected_services": list(self.settings.expected_services),
                "ci_repair_enabled": self.settings.auto_repair_ci,
                "ci_repair_file_allowlist": list(self.settings.auto_repair_files),
                "stateful_services_restartable": False,
                "direct_runtime_code_write": False,
                "auto_draft_pr_on_ci_failure": self.settings.auto_repair_ci,
            },
        }
        if not healthy:
            try:
                logs = self._collect_logs()
            except CommandError as exc:
                logs = f"Log collection failed: {exc}"
            report["diagnostic_logs_redacted"] = redact(logs)
        report_path: Path | None = None
        if not report["overall_healthy"] or actions:
            report_id = uuid.uuid4().hex
            report_path = self.reports_dir / f"incident-{report_id}.json"
            report["report_path"] = str(report_path)
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        # An optional code-repair attempt is triggered only by a completed failed
        # push workflow on main, with an explicit file allowlist and owner opt-in.
        if ci_status and ci_status.get("available") and ci_status.get("conclusion") == "failure":
            run_id = int(ci_status["run_id"])
            if self.store.ci_run_processed(run_id):
                report["actions"].append({"type": "ci_repair", "attempted": False, "reason": "run_already_processed", "run_id": run_id})
            elif not self.settings.auto_repair_ci:
                report["actions"].append({"type": "ci_repair", "attempted": False, "reason": "automatic_ci_repair_disabled", "run_id": run_id})
            elif not self.settings.auto_repair_files:
                report["actions"].append({"type": "ci_repair", "attempted": False, "reason": "explicit_file_allowlist_required", "run_id": run_id})
            elif not any(job.get("redacted_log_excerpt") for job in ci_status.get("failed_jobs", [])):
                report["actions"].append({"type": "ci_repair", "attempted": False, "reason": "failing_job_logs_unavailable", "run_id": run_id})
            elif not self.settings.ai_base_url or not self.settings.ai_model or not self.settings.github_token:
                report["actions"].append({"type": "ci_repair", "attempted": False, "reason": "model_and_github_credentials_required", "run_id": run_id})
            else:
                # Mark before starting so a crashed supervisor cannot create repeated PRs.
                self.store.mark_ci_run_processed(run_id, "repair_started")
                try:
                    failure_summary = json.dumps({
                        "workflow": ci_status.get("workflow"),
                        "run_url": ci_status.get("html_url"),
                        "commit": ci_status.get("head_sha"),
                        "failed_jobs": ci_status.get("failed_jobs", []),
                    }, indent=2)
                    result = propose_repair(
                        self.settings,
                        request=("The latest completed push workflow on main failed. Inspect the supplied allowlisted files and the redacted failing-job log excerpts, "
                                 "make the smallest fix that addresses this CI failure, preserve security invariants, and add no new files.\n\n"
                                 + failure_summary),
                        files=list(self.settings.auto_repair_files),
                        report_path=report_path,
                        publish_pr=True,
                    )
                    action = {"type": "ci_repair", "attempted": True, "success": True, "run_id": run_id, "result": result}
                    self.store.mark_ci_run_processed(run_id, "draft_pr_created")
                except (RepairError, RuntimeError, OSError) as exc:
                    action = {"type": "ci_repair", "attempted": True, "success": False, "run_id": run_id, "error": redact(str(exc))[:1500]}
                    self.store.mark_ci_run_processed(run_id, "repair_failed")
                report["actions"].append(action)
        if report_path is not None:
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        summary = "healthy" if report["overall_healthy"] else (
            f"runtime_healthy={healthy}; ci_state={ci_state}; probe_ok={health.get('ok')}; "
            f"services={','.join(bad_services) or 'none'}"
        )
        self.store.record_check(bool(report["overall_healthy"]), str(report_path or ""), summary)
        LOG.info("CEREPATH check: %s (report=%s)", summary, report_path)
        return report
