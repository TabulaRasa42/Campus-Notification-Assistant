"""Reproduce the agent's explicit review of ac834a0's seven authorized trials.

These are annotations after reading every response, not an automatic semantic grader.
"""

import hashlib
import json
from pathlib import Path


def main():
    folder = Path(__file__).resolve().parent
    raw_bytes = (folder / "issue-12-precheck-v4-raw.json").read_bytes()
    raw = json.loads(raw_bytes)
    corpus = json.loads((folder / "issue-12-cases-v2-proposal.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in corpus["cases"]}
    grades = []
    notes = {
        "M01": "报名事项及缺年截止正确；学生证/校园卡是参赛备查，不能挂到报名材料。",
        "M04": "申请表提交，两证任选一原件携带核验，申请阶段及完整原句依据均保留。",
        "M10": "原件证件组保留斜杠并明确关系待核对；另交一份已签字申请表，依据覆盖申请阶段；确认单排除。",
    }
    for run in raw["runs"]:
        errors = []
        if run["case"] == "M01":
            errors.append(
                {
                    "category": "false_inclusion_or_misattribution",
                    "location": "draft.item.materials[0]",
                    "note": "误收学生证/校园卡。引文真实但支持参赛备查，不支持报名时自行准备或携带；预期材料为空。关系uncertain与引用matched不能抵消归属错误。",
                }
            )
        grades.append(
            {
                "case": run["case"],
                "trial": run["trial"],
                "reviewer": "Codex agent; not independent human acceptance",
                "passed_agent_review": not errors,
                "checks": {
                    "action": "只有当前报名／申请事项，无额外资格或签署事项",
                    "deadline": "符合冻结时间规则；M01保留缺年原话并待确认，M04/M10明确日期且引用匹配",
                    "materials": notes[run["case"]],
                    "must_exclude": "M01误收参赛备查证件；其余排除项未收录"
                    if errors
                    else "排除项未收录",
                    "facts": "逐项核对条件、阶段、责任、数量、原件、关系及引用；剩余错误见errors",
                },
                "errors": errors,
                "actual": run.get("draft"),
                "raw_response_reference": f"issue-12-precheck-v4-raw.json:{run['case']}/trial-{run['trial']}",
                "expected": cases[run["case"]]["expected"],
            }
        )
    report = {
        "candidate_commit": raw["candidate_commit"],
        "prompt_sha256": raw["prompt_sha256"],
        "evaluation_kind": "development_precheck_not_final",
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "review_status": "agent_review_complete; independent_human_acceptance_pending",
        "passed_agent_review": sum(g["passed_agent_review"] for g in grades),
        "total": len(grades),
        "gate": "NOT_FINAL; 6/7, failed the seven-trial precheck; formal stage not run",
        "http_attempts": raw["http_attempts"],
        "usage": raw["usage"],
        "error_counts_by_trial": {"false_inclusion_or_misattribution": 1},
        "runs": grades,
    }
    (folder / "issue-12-precheck-v4-grades.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in report.items() if k != "runs"}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
