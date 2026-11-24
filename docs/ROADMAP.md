# Roadmap

## v0.1 — Independent supervisor
- [x] Separate Python process and repository
- [x] Loopback HTTP health probe and Docker Compose state inspection
- [x] Fixed in-container API liveness and Nginx API-proxy probes
- [x] Bounded, redacted incident reports and SQLite ledger
- [x] Disabled-by-default, allowlisted recovery for exited, unhealthy, missing or repeatedly unresponsive stateless services
- [x] Cooldown after successful and failed recovery attempts; no image builds/pulls during missing-service recovery
- [x] Optional model-assisted incident diagnosis
- [x] Explicit file-scoped patch generation in temporary clone
- [x] Isolated backend compile/test validation gate
- [x] Isolated frontend production build gate for patched frontend files
- [x] Optional draft PR publishing; no merge/deploy
- [x] Optional GitHub Actions main-branch failure monitoring
- [x] One-shot, opt-in CI repair with an explicit file allowlist

## v0.2 — Synthetic application checks
- [ ] Authenticated owner-session probe without exposing session tokens in reports
- [ ] Synthetic chat request using a stub provider (no paid API calls)
- [ ] Teach/review lifecycle test against a disposable PostgreSQL database
- [ ] Playwright smoke suite against isolated Compose stack
- [ ] Incident deduplication and alert notifications

## v0.3 — Stronger repair loop
- [x] Fetch bounded, redacted GitHub Actions failed-job logs through the read-only Actions API
- [ ] Automatically map failing stack traces/tests to a narrow source-file allowlist
- [ ] Automatically select a narrow file context from stack traces and failing test metadata
- [ ] Patch evaluation with regression tests and security-policy checks
- [ ] Patch provenance, reproducible test manifest and report attachment to PR
- [ ] Dependency vulnerability and license scanning

## v1.0 — Controlled maintenance
- [ ] Per-action owner approvals and signed repair attestations
- [ ] Independent reviewer/verifier model, separated from patch author
- [ ] Containerized Guardian service with a narrowly scoped host supervisor bridge
- [ ] Health SLOs, alerting and operational dashboards
- [ ] Disaster-recovery and backup verification workflow
- [ ] External security review

## Never automate by default
- Database volume deletion or restore
- PostgreSQL schema rollback
- Secrets/permission changes
- PR merge, release, deployment or production data migration
