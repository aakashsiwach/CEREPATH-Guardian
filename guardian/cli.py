from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
from pathlib import Path

from .config import Settings
from .diagnose import ModelNotConfigured, diagnose_report
from .monitor import Guardian
from .repair import RepairError, propose_repair


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cerepath-guardian", description="Independent CEREPATH reliability supervisor")
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="Path to Guardian environment file")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Run one monitoring and diagnostics pass")
    sub.add_parser("run", help="Monitor continuously")
    report = sub.add_parser("report", help="Print the latest incident report")
    report.add_argument("--path", type=Path)
    diagnose = sub.add_parser("diagnose", help="Ask the configured model to analyze a redacted incident report")
    diagnose.add_argument("report", type=Path)
    repair = sub.add_parser("repair", help="Propose a bounded source patch in a disposable clone")
    repair.add_argument("--request", required=True)
    repair.add_argument("--files", nargs="+", required=True, help="Existing repo-relative files the model may edit")
    repair.add_argument("--report", type=Path)
    repair.add_argument("--publish-pr", action="store_true", help="Push a branch and open a draft PR after tests pass")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        settings = Settings.load(args.env_file)
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        from logging.handlers import RotatingFileHandler
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            handlers=[
                logging.StreamHandler(sys.stderr),
                RotatingFileHandler(settings.state_dir / "guardian.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
            ],
            force=True,
        )
        if args.command in {"check", "run", "report"}:
            guardian = Guardian(settings)
        if args.command == "check":
            report = guardian.check_once()
            print(json.dumps(report, indent=2))
            return 0 if report.get("overall_healthy") else 1
        if args.command == "run":
            stop = False
            def request_stop(*_: object) -> None:
                nonlocal stop
                stop = True
            signal.signal(signal.SIGINT, request_stop)
            if hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, request_stop)
            while not stop:
                try:
                    guardian.check_once()
                except Exception as exc:  # Keep the supervisor alive after one failed monitoring pass.
                    logging.getLogger("cerepath_guardian").error(
                        "Monitoring pass failed with %s; retrying on the next interval", type(exc).__name__
                    )
                for _ in range(settings.check_interval):
                    if stop:
                        break
                    time.sleep(1)
            return 0
        if args.command == "report":
            path = args.path or guardian.store.latest_report()
            if not path or not path.is_file():
                print("No incident report found", file=sys.stderr)
                return 2
            print(path.read_text(encoding="utf-8"))
            return 0
        if args.command == "diagnose":
            print(diagnose_report(settings, args.report))
            return 0
        if args.command == "repair":
            result = propose_repair(
                settings, request=args.request, files=args.files,
                report_path=args.report, publish_pr=args.publish_pr,
            )
            print(json.dumps(result, indent=2))
            return 0
    except (ValueError, OSError, ModelNotConfigured, RepairError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
