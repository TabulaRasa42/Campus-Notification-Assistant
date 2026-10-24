"""Capture a fixed candidate, only after explicit user approval.

Default invocation checks the manifest without accessing credentials or network.
Use --run only for an authorized batch. Never overwrite or replace failed trials.
The default is 24 final trials; precheck modes are development trials, never a final pass.
"""

import argparse
import hashlib
import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

from campus_notification_assistant.credentials import Credentials
from campus_notification_assistant.deepseek import PROMPT
from campus_notification_assistant.errors import ServiceError
from campus_notification_assistant.service import NotificationService

FROZEN = {
    "issue-12-cases.json": "d50ca6064962292be6b28ff0eb34527789f467854f7eb718bdc35bf19e6d0787",
    "issue-12-protocol.md": "c7894c17271775aeacbbaf75289a1b8d36fb5bce52057143ba385bca67e9e928",
}


def now():
    return datetime.now(UTC).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


class CaptureTransport(httpx.BaseTransport):
    def __init__(self, run, checkpoint):
        self.inner = httpx.HTTPTransport()
        self.run = run
        self.checkpoint = checkpoint

    def handle_request(self, request):
        config = json.loads(request.content)
        attempt = {
            "started_at": now(),
            "state": "in_progress",
            "request_config": {k: v for k, v in config.items() if k != "messages"},
        }
        self.run["attempts"].append(attempt)
        self.checkpoint()
        try:
            response = self.inner.handle_request(request)
            response.read()
            attempt.update(http_status=response.status_code, state="received")
            if response.is_success:
                try:
                    body = response.json()
                    attempt.update(
                        response_model=body.get("model"),
                        choices=body.get("choices"),
                        usage=body.get("usage"),
                    )
                except ValueError:
                    attempt["invalid_json"] = True
            return response
        except httpx.TransportError as exc:
            attempt.update(state="transport_error", transport_error_type=type(exc).__name__)
            raise
        finally:
            attempt["finished_at"] = now()
            self.checkpoint()

    def close(self):
        self.inner.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, help="Exact frozen commit SHA")
    parser.add_argument("--run", action="store_true", help="Make the explicitly authorized calls")
    parser.add_argument("--output", default="issue-12-candidate-raw.json")
    parser.add_argument(
        "--suite", help="Explicitly approved alternate frozen suite manifest filename"
    )
    precheck = parser.add_mutually_exclusive_group()
    precheck.add_argument(
        "--precheck", action="store_true", help="Seven consumed-case trials, not final evaluation"
    )
    precheck.add_argument(
        "--m01-precheck", action="store_true", help="Only consumed M01, three development trials"
    )
    args = parser.parse_args()
    folder = Path(__file__).resolve().parent
    root = folder.parent.parent
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", f"{args.candidate}^{{commit}}"], cwd=root, text=True
    ).strip()
    if args.candidate != commit:
        raise SystemExit("Provide the full candidate SHA, not a moving branch or short ref.")
    subprocess.run(
        ["git", "diff", "--exit-code", "--quiet", commit, "--", "src", "tests"],
        cwd=root,
        check=True,
    )
    corpus_name = "issue-12-cases.json"
    frozen_files = FROZEN
    suite_identity = "materials-v1"
    if args.suite:
        suite_path = folder / args.suite
        if suite_path.parent.resolve() != folder:
            raise SystemExit("Suite manifest must be within the evaluation folder.")
        suite_bytes = suite_path.read_bytes()
        suite = json.loads(suite_bytes)
        corpus_name = suite["corpus"]
        frozen_files = suite["lf_sha256"]
        if set(frozen_files) != {corpus_name, suite["protocol"]}:
            raise SystemExit("Suite must freeze exactly its corpus and protocol.")
        suite_identity = {"filename": args.suite, "sha256": digest(suite_bytes)}
    hashes = {}
    for name, frozen in frozen_files.items():
        if (folder / name).parent.resolve() != folder:
            raise SystemExit("Frozen inputs must be within the evaluation folder.")
        content = (folder / name).read_bytes()
        hashes[name] = {
            "bytes_sha256": digest(content),
            "lf_sha256": digest(content.replace(b"\r\n", b"\n")),
        }
        if hashes[name]["lf_sha256"] != frozen:
            raise SystemExit(f"Frozen facts/protocol changed: {name}")
    corpus = json.loads((folder / corpus_name).read_text(encoding="utf-8"))
    runs = [
        {"case": case["id"], "trial": trial, "outcome": "pending", "attempts": []}
        for case in corpus["cases"]
        for trial in range(1, 4)
    ]
    if len(runs) != 24 or sum(c["split"] == "holdout" for c in corpus["cases"]) != 2:
        raise SystemExit("Expected eight cases, three trials each, including two holdouts.")
    if args.precheck:
        runs = [
            r
            for r in runs
            if r["case"] in {"M04", "M10"} or (r["case"] == "M01" and r["trial"] == 1)
        ]
        if len(runs) != 7:
            raise SystemExit("Precheck requires consumed M01 once and M04/M10 three times each.")
    if args.m01_precheck:
        runs = [r for r in runs if r["case"] == "M01"]
        if len(runs) != 3:
            raise SystemExit("M01 precheck requires exactly three consumed-case trials.")
    report = {
        "candidate_commit": commit,
        "suite": suite_identity,
        "captured_at": now(),
        "frozen_files": hashes,
        "prompt": PROMPT,
        "prompt_sha256": digest(PROMPT.encode()),
        "status": "manifest_checked_no_calls",
        "grading": "pending_agent_and_human_review",
        "request_scope": "Only current source and production prompt; no expected or saved todos",
        "evaluation_kind": (
            "development_m01_precheck_not_final"
            if args.m01_precheck
            else "development_precheck_not_final"
            if args.precheck
            else "final_candidate"
        ),
        "planned_trials": len(runs),
        "maximum_http_attempts": 2 * len(runs),
        "runs": runs,
    }
    if not args.run:
        print(
            json.dumps(
                {k: v for k, v in report.items() if k not in {"runs", "prompt"}},
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    destination = folder / args.output
    if destination.parent.resolve() != folder:
        raise SystemExit("Output must be a new filename within the evaluation folder.")
    with destination.open("x", encoding="utf-8"):
        pass

    def save():
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(destination)

    report["status"] = "running"
    save()
    try:
        try:
            key = Credentials().read()
        except ServiceError:
            report["status"] = "blocked_credential_access"
            return
        if not key:
            report["status"] = "blocked_missing_credential"
            return
        sources = {case["id"]: case["source"] for case in corpus["cases"]}
        with tempfile.TemporaryDirectory(prefix="campus-issue12-candidate-") as temp:
            for run in runs:
                run.update(outcome="in_progress", started_at=now())
                save()
                transport = CaptureTransport(run, save)
                try:
                    service = NotificationService(
                        Path(temp) / "evaluation.sqlite3", transport=transport
                    )
                    service.set_source(sources[run["case"]])
                    draft = service.extract(api_key=key)
                    run.update(
                        outcome="success", draft=draft.model_dump(mode="json") if draft else None
                    )
                except ServiceError as exc:
                    run.update(outcome="service_error", error_code=exc.code, message=str(exc))
                except Exception as exc:  # noqa: BLE001 - no response bodies or secrets in errors
                    run.update(outcome="capture_error", error_type=type(exc).__name__)
                run["finished_at"] = now()
                save()
                print(run["case"], run["trial"], run["outcome"], flush=True)
                if run.get("error_code") in {
                    "invalid_key",
                    "permission_denied",
                    "insufficient_balance",
                    "rate_limited",
                }:
                    report["status"] = "blocked_provider"
                    return
        report["status"] = "captured_not_graded"
    except BaseException:  # retain interruption and pending trials, never replace them
        report["status"] = "interrupted_or_capture_failed"
        raise
    finally:
        report["finished_at"] = now()
        report["http_attempts"] = sum(len(r["attempts"]) for r in runs)
        report["usage"] = {
            k: sum((a.get("usage") or {}).get(k, 0) for r in runs for a in r["attempts"])
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        save()


if __name__ == "__main__":
    main()
