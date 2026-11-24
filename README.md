# CEREPATH Guardian

Copyright 2026 Aakash Siwach. Licensed under Apache-2.0.

**An independent reliability supervisor and bounded code-repair system for CEREPATH.**

Guardian runs as a separate process and repository. It does not live inside the CEREPATH API container, does not have access to the CEREPATH database volume, and does not receive provider credentials from the CEREPATH `.env` file.

## What it does

- Probes the published CEREPATH API health endpoint.
- Reads Docker Compose service state using fixed, read-only commands.
- Collects bounded container logs and redacts common credentials before writing incident reports.
- Keeps a local SQLite incident and restart-cooldown ledger.
- Runs fixed liveness probes inside the API container and through the Nginx `/api/health` route, in addition to Docker healthchecks for PostgreSQL, browser-agent and egress-proxy. A passing end-to-end host health probe corroborates a transient API/web `docker exec` probe failure; the raw probe error remains in the report for diagnosis and does not create a false restart incident.
- Optionally restarts allowlisted, non-database services reported as `exited`, `dead`, `unhealthy` or failing repeated in-container probes, or starts a missing allowlisted service from its existing local image, after repeated failures and a cooldown. The next monitoring pass verifies the outcome.
- Uses an optional OpenAI-compatible model for incident diagnosis.
- Generates a narrowly scoped unified diff in a disposable clone of CEREPATH. Only explicitly listed existing files may be modified.
- Validates the patch with `git apply --check`, `git diff --check`, isolated compilation, backend non-integration tests and (for frontend changes) a production build.
- Can monitor the newest `push` workflow on `main` through the GitHub Actions API, including queued and in-progress states.
- Can optionally attempt one repair for a failed main-branch CI run using a configured, explicit file allowlist, then open a **draft pull request** only after validation.

## What it will never do

- Never restart PostgreSQL or delete/replace Docker volumes.
- Never run arbitrary shell commands returned by an LLM.
- Never mount the Docker socket into a Guardian container (Guardian runs on the host and invokes a fixed Docker Compose command allowlist).
- Never receive or read CEREPATH `.env` values.
- Never commit to `main`, merge a PR, auto-deploy code, or alter the running CEREPATH checkout.
- Never execute a model-generated patch on the live application.
- Never promote its own patch to trusted code. A human reviews every repair PR.

Guardian is an early-development reliability tool, not a production-grade autonomous software engineer. Model-generated patches can be wrong. Full PostgreSQL integration tests and browser end-to-end tests remain separate release gates.

## Requirements

- Python 3.11+
- Docker Desktop / Docker Engine with the `docker compose` plugin
- Git
- Docker build support for ephemeral test images
- Optional: GitHub CLI (`gh`) and a fine-grained GitHub token for draft PR publishing
- Optional: an OpenAI-compatible model endpoint (hosted provider or local Ollama)

On Windows, run commands from PowerShell. Docker Desktop must be running, and the Compose directory must point at the local CEREPATH checkout.

## Install

```powershell
cd path\to\cerepath-guardian
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
Copy-Item .env.example .env
```

Edit `.env` and set `CEREPATH_COMPOSE_DIR` to the local CEREPATH path, for example:

```dotenv
CEREPATH_COMPOSE_DIR=G:\My Work\Claude\Projects\CEREPATH
```

Guardian does not open, copy, or persist CEREPATH's `.env`. The Docker Compose CLI may read that file itself for Compose variable interpolation when Guardian runs fixed `docker compose` commands; Guardian does not capture expanded environment values.

### Create the separate GitHub repository

After extracting this folder, authenticate `gh` with `gh auth login`, then run one of:

```powershell
.\create-repo.ps1                 # private repository by default
.\create-repo.ps1 -Visibility public
```

The script creates `aakashsiwach/cerepath-guardian` and pushes the initial `main` branch. The assistant workspace cannot create a new GitHub repository through the currently available GitHub connector, so this final account-side creation step must be run from your machine. The script configures a repository-local commit identity only when one is not already set; it does not change your global Git identity.

## Run a check

```powershell
python -m guardian --env-file .env check
python -m guardian --env-file .env report
```

The report is written under `GUARDIAN_STATE_DIR/reports/`. Logs are bounded and redacted. `check` exits with code 1 if runtime health or an enabled CI check is failing/unavailable. Review the report before sharing it; redaction is defense-in-depth, not a guarantee that every sensitive value is removed.

Start continuous monitoring:

```powershell
python -m guardian --env-file .env run
```

Run it in a dedicated terminal first. Guardian writes rotating logs to `data/guardian.log` and stores its local incident ledger under `GUARDIAN_STATE_DIR`.

After you have verified observe-only monitoring, you can optionally install a current-user Windows Scheduled Task (no administrator privileges required):

```powershell
.\install-task.ps1
Get-ScheduledTask -TaskName "CEREPATH Guardian" | Get-ScheduledTaskInfo
```

The installer now requests an immediate start after registering the task; it also starts at each subsequent sign-in. Remove it with `.\uninstall-task.ps1` (no Guardian state or reports are deleted).

## Runtime recovery policy

Defaults are observe-only:

```dotenv
AUTO_RESTART_SERVICES=false
ALLOWED_RESTART_SERVICES=api,web,browser-agent,egress-proxy
FAILURES_BEFORE_RESTART=3
RESTART_COOLDOWN_SECONDS=600
```

To opt into bounded recovery, set `AUTO_RESTART_SERVICES=true`. Guardian will only act after the configured consecutive failures: an existing allowlisted stateless container reported `exited`, `dead` or `unhealthy`, or failing the fixed API/Nginx in-container probe, is restarted; a missing allowlisted service is started with `docker compose up -d --no-deps --no-build --pull never <service>` using only a local image (no build or pull). A failed host HTTP probe by itself does not trigger a restart. Only `api`, `web`, `browser-agent` and `egress-proxy` are accepted in the allowlist. PostgreSQL is never restarted or recreated.

## Model-assisted diagnosis

Set an OpenAI-compatible endpoint and model in Guardian's own `.env`:

```dotenv
AI_BASE_URL=http://127.0.0.1:11434/v1
AI_MODEL=qwen2.5-coder:7b
AI_API_KEY=
```

Then:

```powershell
python -m guardian --env-file .env diagnose .\data\reports\incident-<id>.json
```

Only a bounded incident summary and redacted log excerpt are sent to the configured model. Never point Guardian at a hosted model unless you have reviewed the data-sharing implications.

## Code repair workflow

A repair requires a specific failure description and explicit file allowlist. Example:

```powershell
python -m guardian --env-file .env repair `
  --request "Fix the API regression shown in the attached incident report. Preserve the authentication and owner-scope guarantees." `
  --report .\data\reports\incident-<id>.json `
  --files backend/app/intelligence/mind.py backend/tests/test_mind.py
```

Guardian clones the public CEREPATH repository into a temporary workspace, asks the configured model for a unified diff, rejects edits outside the explicit file allowlist, validates the patch, and runs backend checks in a temporary Docker image. It does not change the local CEREPATH checkout. Failed workspaces are intentionally preserved for investigation; they contain no configured provider or application secrets.

To explicitly publish a **draft PR** after validation:

1. Install and authenticate the GitHub CLI prerequisites.
2. Create a fine-grained GitHub token scoped only to `aakashsiwach/CEREPATH` with Contents read/write, Pull requests read/write and Metadata read.
3. Store it in Guardian's `.env` as `GITHUB_TOKEN` (never in CEREPATH's `.env`).
4. Run the same repair command with `--publish-pr`.

```powershell
python -m guardian --env-file .env repair --request "..." --files backend/app/example.py --publish-pr
```

The branch is pushed and a draft PR is opened. Guardian never merges it. Review every line, run PostgreSQL migration/integration tests, and validate on a disposable CEREPATH installation before merging.

### Optional CI-driven repair

Guardian polls the newest `push` workflow on `main`; while it is queued or running it waits for a final conclusion. To opt in to repair attempts after a completed failure:

```dotenv
CI_MONITOR_ENABLED=true
AUTO_REPAIR_CI=true
AUTO_REPAIR_FILES=backend/app/intelligence/specific_module.py,backend/tests/test_specific_module.py
```

Use a token with Actions read in addition to the Contents and Pull requests permissions required to publish a PR. A failed workflow is processed at most once. The configured file list is the hard edit allowlist; a failed workflow alone never gives the model permission to change arbitrary repository files. The repair loop only creates a draft PR after the sandbox gate passes. Keep this off until the manual repair path has been tested end-to-end.

## Architecture

```text
                 Separate process / separate repository
┌─────────────────────────────────────────────────────────────────┐
│ CEREPATH Guardian                                                │
│                                                                  │
│ Health probe ── Docker Compose state ── Redacted diagnostics     │
│      │                  │                     │                  │
│      └──────────── Incident ledger / reports ─┘                  │
│                             │                                    │
│                   Optional model diagnosis                      │
│                             │                                    │
│          Isolated clone → explicit file allowlist → patch        │
│                             │                                    │
│               Docker-based validation sandbox                   │
│                             │                                    │
│                 Optional draft pull request                     │
└─────────────────────────────────────────────────────────────────┘
                 │ observe / fixed restart allowlist
                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ CEREPATH                                                         │
│ Nginx → FastAPI → PostgreSQL / pgvector                          │
│       Browser agent + restricted egress proxy                    │
└─────────────────────────────────────────────────────────────────┘
```

The runtime recovery plane and code repair plane are deliberately distinct. Restarting a failed process is not the same as repairing source code. A green health check is not proof that business workflows work; future iterations should add authenticated synthetic probes and disposable-database integration tests.

## Security notes

- Run Guardian under a dedicated OS account where practical.
- Keep its `.env`, SQLite state and repair workspaces private.
- Never copy CEREPATH's `.env` into Guardian or a repair workspace.
- Do not expose a Guardian HTTP control endpoint; the initial version is a local CLI process.
- Restrict GitHub token permissions and revoke the token when unused.
- Logs can contain personal data even after credential redaction.
- Test the service-restart policy with `AUTO_RESTART_SERVICES=false` first.
- Database restore, volume deletion, migrations, permission changes, merges and deployments always require a separate human-operated procedure.

## Tests

```powershell
python -m pytest -q
```

## Current boundaries

- Health checks target `/api/health` through Nginx and fixed local liveness probes inside the API and web containers; authenticated workflow probes are not yet enabled.
- Backend integration tests requiring PostgreSQL are excluded from the isolated repair gate and must be run separately in CI.
- Frontend source changes trigger a production build in an isolated Docker build stage; CEREPATH currently has no lockfile, so this uses `npm install` and is less reproducible than `npm ci`.
- CI monitoring captures failed workflow/job/step metadata and a bounded redacted excerpt from failed-job logs. It does not yet map stack traces to source files automatically.
- Local target URLs must be loopback HTTP(S) addresses, and the selected Compose file must remain within the configured CEREPATH checkout.
- Guardian's own maintenance is currently handled by its CI and Dependabot updates; it does not autonomously patch or redeploy itself.
- PostgreSQL migration/integration tests and authenticated Playwright workflows are still separate release gates.
- No automatic PR merge, production deploy, database mutation, backup restore or self-update of Guardian.
