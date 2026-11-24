from pathlib import Path

import pytest

from guardian.repair import RepairError, _safe_relative_files, _validate_diff_paths


def test_repair_paths_must_be_explicit_and_inside_repo(tmp_path: Path) -> None:
    source = tmp_path / "backend" / "app.py"
    source.parent.mkdir()
    source.write_text("print('ok')", encoding="utf-8")
    assert _safe_relative_files(tmp_path, ["backend/app.py"]) == [source]
    with pytest.raises(RepairError):
        _safe_relative_files(tmp_path, ["../secrets.env"])


def test_patch_cannot_escape_explicit_file_allowlist() -> None:
    diff = "--- a/backend/app.py\n+++ b/backend/app.py\n@@ -1 +1 @@\n-a\n+b\n"
    _validate_diff_paths(diff, {"backend/app.py"})
    dangerous = diff.replace("backend/app.py", "backend/config.py")
    with pytest.raises(RepairError):
        _validate_diff_paths(dangerous, {"backend/app.py"})


def test_patch_cannot_add_or_delete_files() -> None:
    diff = "--- /dev/null\n+++ b/backend/new.py\n@@ -0,0 +1 @@\n+x\n"
    with pytest.raises(RepairError):
        _validate_diff_paths(diff, {"backend/new.py"})


def test_security_and_migration_paths_are_protected(tmp_path: Path) -> None:
    security = tmp_path / "backend" / "app" / "security.py"
    security.parent.mkdir(parents=True)
    security.write_text("# security", encoding="utf-8")
    migrations = tmp_path / "backend" / "app" / "migrations" / "007.sql"
    migrations.parent.mkdir(parents=True)
    migrations.write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(RepairError):
        _safe_relative_files(tmp_path, ["backend/app/security.py"])
    with pytest.raises(RepairError):
        _safe_relative_files(tmp_path, ["backend/app/migrations/007.sql"])


def test_patch_cannot_rename_or_change_file_mode() -> None:
    renamed = (
        "diff --git a/backend/app.py b/backend/app2.py\n"
        "similarity index 100%\n"
        "rename from backend/app.py\n"
        "rename to backend/app2.py\n"
    )
    with pytest.raises(RepairError):
        _validate_diff_paths(renamed, {"backend/app.py", "backend/app2.py"})

    mode_change = (
        "diff --git a/backend/app.py b/backend/app.py\n"
        "old mode 100644\nnew mode 100755\n"
        "--- a/backend/app.py\n+++ b/backend/app.py\n@@ -1 +1 @@\n-a\n+b\n"
    )
    with pytest.raises(RepairError):
        _validate_diff_paths(mode_change, {"backend/app.py"})


def test_frontend_sandbox_build_is_defined_and_uses_networkless_build(tmp_path: Path, monkeypatch) -> None:
    from guardian.repair import _sandbox_frontend_build

    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    calls = []

    def fake_run(command, cwd, **kwargs):
        calls.append(command)
        return "frontend build succeeded"

    monkeypatch.setattr("guardian.repair._run", fake_run)
    monkeypatch.setattr("guardian.repair.subprocess.run", lambda *args, **kwargs: None)
    output = _sandbox_frontend_build(tmp_path)
    assert "succeeded" in output
    assert calls[0][:2] == ["docker", "build"]
    dockerfile = frontend / ".guardian.Dockerfile"
    assert not dockerfile.exists()


def test_protected_authentication_and_database_files_are_rejected(tmp_path: Path) -> None:
    authentication = tmp_path / "backend" / "app" / "authentication.py"
    database = tmp_path / "backend" / "app" / "database.py"
    authentication.parent.mkdir(parents=True)
    authentication.write_text("# auth", encoding="utf-8")
    database.write_text("# database", encoding="utf-8")
    with pytest.raises(RepairError):
        _safe_relative_files(tmp_path, ["backend/app/authentication.py"])
    with pytest.raises(RepairError):
        _safe_relative_files(tmp_path, ["backend/app/database.py"])


def test_repair_paths_reject_windows_drive_and_alternate_stream_paths(tmp_path: Path) -> None:
    with pytest.raises(RepairError):
        _safe_relative_files(tmp_path, [r"C:\Windows\system.ini"])
    with pytest.raises(RepairError):
        _safe_relative_files(tmp_path, [r"backend\app.py:alternate-stream"])
