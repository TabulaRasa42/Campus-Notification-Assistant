"""Read-only development-set capture for Issue #12; never saves a Todo.

Run from the worktree with its locked Python environment. Only the six approved
development sources are sent to the existing provider, three times each. The
two holdout cases are never sent by this script. Credentials are read in memory.
"""

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


class CaptureTransport(httpx.BaseTransport):
    def __init__(self):
        self.inner = httpx.HTTPTransport()
        self.attempts = []

    def handle_request(self, request):
        config = json.loads(request.content)
        attempt = {
            "request_config": {key: value for key, value in config.items() if key != "messages"}
        }
        self.attempts.append(attempt)
        try:
            response = self.inner.handle_request(request)
            response.read()
            attempt["http_status"] = response.status_code
            if response.is_success:
                try:
                    body = response.json()
                    attempt["response_model"] = body.get("model")
                    attempt["choices"] = body.get("choices")
                    attempt["usage"] = body.get("usage")
                except ValueError:
                    attempt["invalid_json"] = True
            return response
        except httpx.TransportError as exc:
            attempt["transport_error_type"] = type(exc).__name__
            raise

    def close(self):
        self.inner.close()


def main():
    folder = Path(__file__).resolve().parent
    destination = folder / "issue-12-baseline-raw.json"
    if destination.exists():
        raise SystemExit("Baseline already exists; refusing to overwrite previous trials.")
    corpus_bytes = (folder / "issue-12-cases.json").read_bytes()
    corpus = json.loads(corpus_bytes)
    report = {
        "captured_at": datetime.now(UTC).isoformat(),
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "prompt": PROMPT,
        "protocol_sha256": hashlib.sha256(
            (folder / "issue-12-protocol.md").read_bytes()
        ).hexdigest(),
        "status": "running",
        "grading": "pending; captured outputs are not a quality pass",
        "runs": [],
    }

    def save():
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        key = Credentials().read()
    except ServiceError:
        report["status"] = "blocked_credential_access"
        save()
        print(report["status"], flush=True)
        return
    if not key:
        report["status"] = "blocked_missing_credential"
        save()
        print(report["status"], flush=True)
        return
    save()
    with tempfile.TemporaryDirectory(prefix="campus-quality-") as temp:
        for case in corpus["cases"]:
            if case["split"] != "development":
                continue
            for trial in range(1, 4):
                transport = CaptureTransport()
                run = {
                    "case": case["id"],
                    "trial": trial,
                    "started_at": datetime.now(UTC).isoformat(),
                }
                service = NotificationService(Path(temp) / "temporary.sqlite3", transport=transport)
                service.set_source(case["source"])
                try:
                    draft = service.extract(api_key=key)
                    run["outcome"] = "success"
                    run["draft"] = draft.model_dump(mode="json") if draft else None
                except ServiceError as exc:
                    run["outcome"] = "service_error"
                    run["error_code"] = exc.code
                    run["message"] = str(exc)
                except Exception as exc:  # noqa: BLE001 - retain failed trials without secret traces
                    run["outcome"] = "capture_error"
                    run["error_type"] = type(exc).__name__
                run["attempts"] = transport.attempts
                run["finished_at"] = datetime.now(UTC).isoformat()
                report["runs"].append(run)
                save()
                print(case["id"], trial, run["outcome"], flush=True)
                if run.get("error_code") in {
                    "invalid_key",
                    "permission_denied",
                    "insufficient_balance",
                    "rate_limited",
                }:
                    report["status"] = "blocked_provider"
                    save()
                    return
    report["status"] = "captured"
    save()


if __name__ == "__main__":
    main()
