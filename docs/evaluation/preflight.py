"""Offline mechanical gates. Semantic review and human authorization remain explicit."""

import hashlib
import json

from pydantic import ValidationError

from campus_notification_assistant.models import Extraction


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit(f"invalid_record: {path.name}") from None


def contract_errors(payload):
    errors = []
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return ["items must be an array"]
    for item in payload["items"]:
        if not isinstance(item, dict) or not {"action", "deadline", "materials"} <= item.keys():
            errors.append("item requires action, deadline and materials")
            continue
        fields = [("action", item["action"], {"value", "evidence"})]
        deadline = item["deadline"]
        if deadline is not None:
            fields.append(
                ("deadline", deadline, {"raw", "date", "status", "precision", "evidence"})
            )
        materials = item["materials"]
        if not isinstance(materials, list):
            errors.append("materials must be an array")
            continue
        fields.extend(
            ("material", m, {"value", "requirement", "relation", "evidence"}) for m in materials
        )
        for name, field, required in fields:
            if not isinstance(field, dict):
                errors.append(f"{name} must be an object")
                continue
            errors.extend(f"{name}.{key} is required" for key in sorted(required - field.keys()))
            for key in required & {"value", "raw", "requirement", "evidence"}:
                if not isinstance(field.get(key), str) or not field[key].strip():
                    errors.append(f"{name}.{key} must be nonblank")
            if {"origin", "review"} & field.keys():
                errors.append(f"{name} must not contain application review state")
        if isinstance(deadline, dict):
            certain = deadline.get("status") == "certain"
            if certain != (deadline.get("date") is not None):
                errors.append("deadline date/status disagree")
            if deadline.get("precision") != ("date" if certain else "unknown"):
                errors.append("deadline precision/status disagree")
    try:
        Extraction.model_validate_json(json.dumps(payload))
    except (ValidationError, ValueError, TypeError):
        errors.append("response does not match the application model")
    return errors


def check_examples(folder):
    path = folder / "contract-examples.json"
    record = read_json(path)
    examples = record.get("examples") if isinstance(record, dict) else None
    if not isinstance(examples, list) or not examples:
        raise SystemExit("contract_invalid: missing examples")
    modes = set()
    for example in examples:
        errors = contract_errors(example)
        if errors:
            raise SystemExit("contract_invalid: " + "; ".join(errors))
        for item in example["items"]:
            modes.add(item["deadline"]["status"] if item["deadline"] else "missing")
    if modes != {"certain", "uncertain", "missing"}:
        raise SystemExit(
            "contract_invalid: examples must cover certain, uncertain and missing time"
        )
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def check_holdouts(folder, cases):
    holdouts = {c["id"] for c in cases if c["split"] == "holdout"}
    holdout_sources = {c["source"] for c in cases if c["split"] == "holdout"}
    sources = {}
    for path in sorted(folder.glob("issue-12-cases*.json")):
        for case in read_json(path)["cases"]:
            sources.setdefault(case["id"], set()).add(case["source"])
    ledger = {}
    for path in sorted(folder.glob("*-raw.json")):
        raw = read_json(path)
        if not isinstance(raw, dict) or not isinstance(raw.get("runs"), list):
            raise SystemExit(f"invalid_record: missing runs in {path.name}")
        ledger[path.name] = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        for run in raw["runs"]:
            if not run.get("attempts"):
                continue
            draft = run.get("draft") or {}
            previous_sources = sources.get(run["case"], set()) | {draft.get("source", "")}
            if run["case"] in holdouts or holdout_sources & previous_sources:
                raise SystemExit(f"holdout_consumed: {run['case']} in {path.name}")
    return ledger


def check_development_review(path, folder, candidate, prompt_sha):
    if path is None:
        raise SystemExit("development_review_required: review consumed development results first")
    review = read_json(path)
    if not isinstance(review, dict):
        raise SystemExit("development_review_invalid: expected an object")
    if review.get("unresolved_defects"):
        raise SystemExit("development_defects: fix known defects before formal evaluation")
    if review.get("unresolved_defects") != []:
        raise SystemExit("development_review_invalid: explicitly record unresolved_defects")
    for historical in sorted(folder.glob("*-raw.json")):
        capture = read_json(historical)
        if (
            capture.get("candidate_commit") == candidate
            and capture.get("evaluation_kind", "").startswith("development_")
            and any(
                r.get("outcome") != "success" or r.get("contract_errors")
                for r in capture.get("runs", [])
            )
        ):
            raise SystemExit(f"development_defects: unresolved capture {historical.name}")
    for historical in sorted(folder.glob("*-grades.json")):
        grades = read_json(historical)
        if grades.get("candidate_commit") == candidate and any(
            r.get("passed_agent_review") is False or r.get("errors") for r in grades.get("runs", [])
        ):
            raise SystemExit(f"development_defects: unresolved review {historical.name}")
    raw_name = review.get("raw_file")
    if not isinstance(raw_name, str) or (folder / raw_name).parent.resolve() != folder.resolve():
        raise SystemExit("development_review_invalid: raw_file must name a capture in this folder")
    raw_path = folder / raw_name
    raw = read_json(raw_path)
    raw_sha = hashlib.sha256(raw_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if (
        review.get("candidate_commit") != candidate
        or review.get("prompt_sha256") != prompt_sha
        or review.get("raw_lf_sha256") != raw_sha
        or raw.get("candidate_commit") != candidate
        or raw.get("prompt_sha256") != prompt_sha
        or raw.get("status") != "captured_not_graded"
        or not raw.get("evaluation_kind", "").startswith("development_")
    ):
        raise SystemExit("development_review_mismatch: review must bind this candidate and capture")
    runs, grades = raw.get("runs", []), review.get("runs", [])
    if not runs or len(runs) != len(grades):
        raise SystemExit("development_review_incomplete: review every recorded development trial")
    trial_ids = [(r["case"], r["trial"]) for r in runs]
    grade_ids = [(r["case"], r["trial"]) for r in grades]
    if len(set(trial_ids)) != len(runs) or set(trial_ids) != set(grade_ids):
        raise SystemExit("development_review_incomplete: duplicated or missing trial review")
    grade_by_trial = {(g["case"], g["trial"]): g for g in grades}
    for run in runs:
        grade = grade_by_trial[(run["case"], run["trial"])]
        if (
            run.get("outcome") != "success"
            or not run.get("draft")
            or grade.get("passed_agent_review") is not True
            or grade.get("errors") != []
            or not isinstance(grade.get("reviewer"), str)
            or not grade["reviewer"].strip()
        ):
            raise SystemExit("development_defects: every trial must pass explicit semantic review")
        try:
            content = json.loads(run["attempts"][-1]["choices"][0]["message"]["content"])
        except (KeyError, IndexError, ValueError, TypeError):
            raise SystemExit("contract_invalid: development response is missing") from None
        errors = contract_errors(content)
        if errors:
            raise SystemExit("contract_invalid: " + "; ".join(errors))
    return {
        "review_lf_sha256": hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
        "raw_file": raw_name,
        "raw_lf_sha256": raw_sha,
        "reviewed_trials": len(runs),
    }
