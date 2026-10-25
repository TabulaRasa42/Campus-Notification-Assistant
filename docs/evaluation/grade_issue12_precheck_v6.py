"""Explicit agent annotations for six authorized citation prechecks, not an auto-grader."""

import hashlib
import json
from pathlib import Path


def main():
    folder = Path(__file__).resolve().parent
    raw_bytes = (folder / "issue-12-precheck-v6-raw.json").read_bytes()
    raw = json.loads(raw_bytes)
    cases = {
        c["id"]: c
        for c in json.loads((folder / "issue-12-cases.json").read_text(encoding="utf-8"))["cases"]
    }
    assert [(r["case"], r["trial"]) for r in raw["runs"]] == [
        (case, n) for case in ("M01", "M02") for n in (1, 2, 3)
    ]
    grades = []
    for run in raw["runs"]:
        assert run["outcome"] == "success" and run["draft"]
        errors = []
        model_errors = []
        if (run["case"], run["trial"]) == ("M02", 1):
            errors = [
                {
                    "category": "qualifier_distortion",
                    "location": f"draft.item.materials[{n}].requirement",
                    "note": "说明保留报名时上传及电子版/一张一寸，但遗漏原文明示的所有报名者；主体仅在引用中，不符合可见说明规则。",
                }
                for n in (0, 1)
            ]
            model_errors = errors.copy()
        elif run["case"] == "M02":
            model_errors = [
                {
                    "category": "insufficient_evidence",
                    "location": "response.items[0].materials[*].evidence",
                    "note": "原始引用从报名时上传开始，缺共有主体；应用补足原句后已覆盖所有报名者。",
                }
            ]
        grades.append(
            {
                "case": run["case"],
                "trial": run["trial"],
                "reviewer": "Codex agent; independent human acceptance pending",
                "passed_agent_review": not errors,
                "errors": errors,
                "model_raw_passed_agent_review": not model_errors,
                "model_raw_errors": model_errors,
                "checks": {
                    "action": "M01引文含报名请求或报名截止要求，支持当前报名；不是只有活动日期。M02当前志愿者报名范围正确。",
                    "deadline": "M01缺年保留24:00并待确认；M02明确2026-11-15、日期精度及逐字依据正确。",
                    "materials": "M01材料为空；M02两项均上传，电子版、一张一寸、且关系及阶段正确；M02/1缺共同主体见errors。",
                    "evidence": "文字与位置均匹配；M02/2和3应用已补全原句、保留共同主体。",
                    "must_exclude": "没有参赛证件、保证书、检查报告或背景事项混入报名材料。",
                },
                "actual": run["draft"],
                "expected": cases[run["case"]]["expected"],
            }
        )
    report = {
        "candidate_commit": raw["candidate_commit"],
        "prompt_sha256": raw["prompt_sha256"],
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "evaluation_kind": "development_citation_precheck_not_final",
        "review_status": "agent_review_complete; independent_human_acceptance_pending",
        "gate": "FAILED_PRECHECK; 5/6 application results; not formal acceptance",
        "passed_agent_review": 5,
        "model_raw_passed_agent_review": 3,
        "total": 6,
        "http_attempts": raw["http_attempts"],
        "usage": raw["usage"],
        "runs": grades,
    }
    (folder / "issue-12-precheck-v6-grades.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in report.items() if k != "runs"}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
