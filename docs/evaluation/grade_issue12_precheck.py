"""Render explicit agent annotations for the seven authorized development trials."""

import hashlib
import json
from pathlib import Path


def main():
    folder = Path(__file__).resolve().parent
    raw_bytes = (folder / "issue-12-precheck-v3-raw.json").read_bytes()
    raw = json.loads(raw_bytes)
    corpus = json.loads((folder / "issue-12-cases-v2-proposal.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in corpus["cases"]}
    grades = []
    for run in raw["runs"]:
        failed = run["case"] == "M10" and run["trial"] == 3
        grades.append(
            {
                "case": run["case"],
                "trial": run["trial"],
                "reviewer": "Codex agent; not independent human acceptance",
                "passed_agent_review": not failed,
                "checks": {
                    "action": "当前事项范围正确，引用连续且匹配",
                    "deadline": "日期或缺年待确认均符合规则，引用有效",
                    "materials": "完整名称、条件、阶段、处理及原件／数量保留，引用包含完整阶段",
                    "must_exclude": "无误收录或额外事项",
                    "facts": "M04组选一原件正确；M10第1/2轮明确关系待核对，第3轮遗漏；M01空材料正确",
                },
                "errors": (
                    [
                        {
                            "category": "qualifier_distortion",
                            "location": "draft.item.materials[0].requirement",
                            "note": "保留斜杠，但未明确材料关系待核对。",
                        }
                    ]
                    if failed
                    else []
                ),
                "actual": run.get("draft"),
                "raw_model_outputs": [a.get("choices") for a in run["attempts"]],
                "expected": cases[run["case"]]["expected"],
            }
        )
    report = {
        "candidate_commit": raw["candidate_commit"],
        "prompt_sha256": raw["prompt_sha256"],
        "evaluation_kind": "development_precheck_not_final",
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "review_status": "agent_review_complete; independent_human_acceptance_pending",
        "passed_agent_review": 6,
        "total": len(grades),
        "gate": "NOT_FINAL; 6/7 development checks, not a 24-trial quality pass",
        "http_attempts": raw["http_attempts"],
        "usage": raw["usage"],
        "runs": grades,
    }
    (folder / "issue-12-precheck-v3-grades.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in report.items() if k != "runs"}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
