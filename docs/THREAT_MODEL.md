# Threat model

## Assets

- CEREPATH source and owner data
- Runtime availability and PostgreSQL volume integrity
- Provider keys, bootstrap tokens, browser-internal token and GitHub token
- Incident reports, logs and repair workspaces

## Trust boundaries

1. Guardian is a separate repository/process and must not read CEREPATH's `.env` or database volume.
2. Model output is untrusted data. It is parsed only as a unified diff and never interpreted as commands.
3. Repository source and incident logs are untrusted model input. Prompt injection inside them must not grant new authority.
4. Repair execution occurs in a disposable clone and an ephemeral Docker test image without application secrets.
5. Publishing a branch and opening a draft PR is an explicit CLI action requiring a separately scoped token.

## Threats and controls

| Threat | Control |
|---|---|
| Model proposes destructive shell commands | No model-authored commands are executed |
| Model edits security-sensitive unrelated files | Explicit per-request file allowlist; new/deleted files rejected |
| Repair corrupts running checkout | Repair always uses a fresh temporary clone |
| Test code reads secrets | No app secrets are mounted into test containers |
| Test code phones home | Test stage runs with Docker build network disabled |
| Restart loop | Failure threshold, per-service cooldown after success or failure, default-off switch |
| Unrelated host endpoint or Compose project is targeted | Health URL is loopback-only; Compose file must resolve inside the configured CEREPATH checkout |
| Missing service causes a surprise build or image download | Recovery command uses `--no-build --pull never` and can start only an allowlisted stateless service |
| Data loss from restarting PostgreSQL | Database service explicitly forbidden from automatic restart |
| Secrets leak through logs | Bounded logs and redaction before persistence; review still required |
| Untrusted caller invokes privileged repair | No network control API in the initial release |
| Bot silently merges unsafe change | Draft PR only; no merge/deploy capability |

## Residual risks

- A host user who can modify Guardian's `.env` or executable can change its behavior.
- A Docker daemon user can generally control the host. Guardian therefore runs as a local process and should be launched only by a trusted host account.
- Redaction is heuristic; logs may still contain private content.
- The dependency-install stage of a validation image needs network access. The subsequent source compilation and test stage is `RUN --network=none` and receives no application secrets.
- Tests are not a proof of correctness. Human review and disposable-environment verification remain mandatory.
