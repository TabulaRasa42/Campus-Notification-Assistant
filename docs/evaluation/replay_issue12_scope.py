"""Offline recorded-response replay, never a new model evaluation or semantic score.

Uses only previously consumed cases and HTTP MockTransport, without credentials.
Preserves every original capture and writes a separate, exclusive output file.
"""

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

import httpx

from campus_notification_assistant.errors import ServiceError
from campus_notification_assistant.service import NotificationService

CAPTURES = (
    "issue-12-baseline-raw.json",
    "issue-12-candidate-raw.json",
    "issue-12-candidate-v2-raw.json",
    "issue-12-precheck-v3-raw.json",
    "issue-12-precheck-v4-raw.json",
)


def material_fields(material):
    return {
        key: material.get(key, default)
        for key, default in (
            ("value", ""),
            ("evidence", ""),
            ("requirement", ""),
            ("relation", None),
        )
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--include-later-captures",
        action="store_true",
        help="Also replay the consumed M01 three-trial precheck and final v3 batch",
    )
    args = parser.parse_args()
    folder = Path(__file__).resolve().parent
    root = folder.parent.parent
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    subprocess.run(
        ["git", "diff", "--exit-code", "--quiet", "HEAD", "--", "src", "tests"],
        cwd=root,
        check=True,
    )
    sources = {}
    corpora = ["issue-12-cases.json", "issue-12-cases-v2-proposal.json"]
    captures = list(CAPTURES)
    if args.include_later_captures:
        corpora.append("issue-12-cases-v3-proposal.json")
        captures.extend(("issue-12-precheck-v5-raw.json", "issue-12-candidate-v3-raw.json"))
    for name in corpora:
        for case in json.loads((folder / name).read_text(encoding="utf-8"))["cases"]:
            sources[case["id"]] = case["source"]
    report = {
        "kind": "offline_recorded_response_replay_not_semantic_acceptance",
        "application_commit": commit,
        "new_api_calls": 0,
        "capture_sha256": {},
        "runs": [],
    }
    with tempfile.TemporaryDirectory(prefix="issue12-offline-") as temporary:
        for name in captures:
            raw = (folder / name).read_bytes()
            report["capture_sha256"][name] = hashlib.sha256(raw).hexdigest()
            for run in json.loads(raw)["runs"]:
                choices = run["attempts"][-1]["choices"]
                body = {"choices": choices}
                service = NotificationService(
                    Path(temporary) / "replay.sqlite3",
                    transport=httpx.MockTransport(
                        lambda request, body=body: httpx.Response(200, json=body)
                    ),
                )
                service.set_source(sources[run["case"]])
                row = {"capture": name, "case": run["case"], "trial": run["trial"]}
                try:
                    draft = service.extract(api_key="offline-no-credentials")
                    row["outcome"] = "draft" if draft else "empty"
                    if draft:
                        item = json.loads(choices[0]["message"]["content"])["items"][0]
                        before = (run.get("draft") or {}).get("item", item)
                        row["before"] = [material_fields(m) for m in before.get("materials", [])]
                        row["after"] = [
                            material_fields(m.model_dump()) for m in draft.item.materials
                        ]
                        row["changed"] = row["before"] != row["after"]
                        row["action_after"] = draft.item.action.model_dump(mode="json")
                        row["action_before"] = before["action"]
                        row["deadline_unchanged"] = (
                            draft.item.deadline.model_dump(mode="json") == before.get("deadline")
                            if "review" in (before.get("deadline") or {})
                            else None
                        )
                        row["confirm_ready"] = draft.item.evidence_ready
                except ServiceError as exc:
                    row.update(outcome="service_error", message=str(exc))
                report["runs"].append(row)
    report["summary"] = {
        "recorded_trials": len(report["runs"]),
        "drafts": sum(r["outcome"] == "draft" for r in report["runs"]),
        "service_errors": sum(r["outcome"] == "service_error" for r in report["runs"]),
        "material_changes": sum(r.get("changed", False) for r in report["runs"]),
        "actions_requiring_review": sum(
            r.get("action_after", {}).get("review") == "invalid" for r in report["runs"]
        ),
    }
    with Path(args.output).open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(report["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
