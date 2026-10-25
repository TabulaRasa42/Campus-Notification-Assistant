"""Render explicit agent annotations after reviewing all 24 frozen-candidate trials.

This reproduces a review, not an automatic semantic grader. Human acceptance is pending.
Original responses and standards remain unchanged, including failed trials.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path


def main():
    folder = Path(__file__).resolve().parent
    raw_bytes = (folder / "issue-12-candidate-v3-raw.json").read_bytes()
    raw = json.loads(raw_bytes)
    corpus = json.loads((folder / "issue-12-cases-v3-proposal.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in corpus["cases"]}
    assert raw["status"] == "captured_not_graded"
    assert len(raw["runs"]) == 24
    assert all(r["outcome"] == "success" and r.get("draft") for r in raw["runs"])
    notes = {
        "M01": "仅校运会报名，材料为空；保留10月18日晚24:00并待确认，不从标题补年。第1轮事项引文不能支持报名动作，见errors。",
        "M02": "报名表电子版和一张一寸照两项均上传，完整说明含所有报名者及报名阶段。第3轮两项引文缺共同主体前文，见errors。",
        "M03": "全体报名表；首次学生报名时提交学生证复印件，往年豁免保留。条件豁免引文与冻结参考一致，不额外强求前句。",
        "M04": "申请时提交表，另携带两证任一原件核验；any及完整阶段依据齐全，非三者任选。",
        "M05": "领取事项；无需提前准备的现场登记表排除，不推断提供方，不新增无需材料状态。",
        "M06": "提前下载打印一份、申请时自行携带、现场签署后提交，完整说明及整句依据均保留。",
        "M11": "全体申请时上传一份PDF表；仅校外居住者另上传证明复印件，校内豁免及跨句先行条件齐全；取证身份证排除。",
        "M12": "办理时自行携带学生证/身份证原件到服务台核验，uncertain明确待核对；另交一张两寸照；完整阶段依据保留，确认单排除。",
    }
    grades = []
    for run in raw["runs"]:
        errors = []
        action_note = "当前事项及对象正确，没有新增背景事项；动作引文支持事项"
        evidence_note = "逐字匹配、可定位，并支持可见说明中的实质限定"
        if run["case"] == "M01" and run["trial"] == 1:
            action_note = "事项名和范围正确，但所引比赛介绍没有报名动作，不能支持该字段"
            evidence_note = "引文字面匹配；事项依据语义不足。时间引用及缺年状态正确"
            errors.append(
                {
                    "category": "insufficient_evidence",
                    "location": "draft.item.action.evidence",
                    "note": "引文只介绍运动会日期、地点和比赛信息，没有报名要求；不能作为报名事项的依据。字面matched未证明语义支持。",
                }
            )
        if run["case"] == "M02" and run["trial"] == 3:
            evidence_note = "两项引文有报名阶段、上传方式和材料限定，但缺说明所继承的所有报名者前文；按保守规则记依据不足，待人工核对"
            for index in (0, 1):
                errors.append(
                    {
                        "category": "insufficient_evidence",
                        "location": f"draft.item.materials[{index}].evidence",
                        "note": "requirement声称所有报名者适用，引文却从报名时上传开始，未包含所有报名者的先行主体。完整引用应含该句前半段；材料说明本身并未丢失该主体。",
                    }
                )
        grades.append(
            {
                "case": run["case"],
                "trial": run["trial"],
                "reviewer": "Codex agent; independent human acceptance pending",
                "passed_agent_review": not errors,
                "semantic_review": "scored",
                "checks": {
                    "action": action_note,
                    "deadline": "M01/M11保留缺年原话且date为空；其余明确完整日期，精度为日期；无补年份、时刻或误用活动日期",
                    "materials": notes[run["case"]],
                    "must_exclude": "逐项核对，无其他阶段文件、资格检查、现场无需准备文件或背景事项混入",
                    "facts": "逐项核对全部expected.facts；M02的single/all单对象状态不改变两项均必需，未按枚举字符差异扣分",
                    "evidence": evidence_note,
                },
                "errors": errors,
                "actual": run["draft"],
                "expected": cases[run["case"]]["expected"],
                "raw_response_reference": f"issue-12-candidate-v3-raw.json:{run['case']}/trial-{run['trial']}",
            }
        )
    counts = Counter(c for g in grades for c in {e["category"] for e in g["errors"]})
    passed = sum(g["passed_agent_review"] for g in grades)
    report = {
        "candidate_commit": raw["candidate_commit"],
        "prompt_sha256": raw["prompt_sha256"],
        "suite": raw["suite"],
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "review_status": "agent_review_complete; independent_human_acceptance_pending",
        "gate": f"FAILED; {passed}/24, below frozen 24/24 threshold",
        "total": len(grades),
        "passed_agent_review": passed,
        "service_successes": 24,
        "development_passed": sum(
            g["passed_agent_review"] for g in grades if g["case"] not in {"M11", "M12"}
        ),
        "holdout_passed": sum(
            g["passed_agent_review"] for g in grades if g["case"] in {"M11", "M12"}
        ),
        "error_counts_by_trial": dict(counts),
        "http_attempts": raw["http_attempts"],
        "usage": raw["usage"],
        "holdout_status": "M11/M12 first called in this batch; evaluated and reviewed, no longer untouched",
        "runs": grades,
    }
    (folder / "issue-12-candidate-v3-grades.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in report.items() if k != "runs"}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
