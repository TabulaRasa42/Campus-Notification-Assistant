"""Render the agent's per-trial v2 review; preserve all original captures."""

import hashlib
import json
from collections import Counter
from pathlib import Path


def main():
    folder = Path(__file__).resolve().parent
    raw_bytes = (folder / "issue-12-candidate-v2-raw.json").read_bytes()
    raw = json.loads(raw_bytes)
    corpus = json.loads((folder / "issue-12-cases-v2-proposal.json").read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in corpus["cases"]}
    grades = []
    for run in raw["runs"]:
        errors = []
        if run["case"] == "M01" and run["trial"] == 1:
            errors.append(
                {
                    "category": "insufficient_evidence",
                    "location": "draft.item.action.evidence",
                    "note": "模型把感叹号后原文的两个空格合并为一个，引用不逐字匹配，应用正确标invalid。未自动补引用，不能确认保存。",
                }
            )
        if run["case"] == "M04":
            errors.append(
                {
                    "category": "insufficient_evidence",
                    "location": "draft.item.materials[1].evidence",
                    "note": "说明完整保留申请阶段、携带核验、任选原件；但证件引用从并携带或携带开始，没有支持申请时阶段的前文。",
                }
            )
        if run["case"] == "M10":
            errors.extend(
                [
                    {
                        "category": "qualifier_distortion",
                        "location": "draft.item.materials[0].requirement",
                        "note": "保留身份证/护照原件及携带窗口核验，但没有明确标为材料关系待核对；原文斜杠未说明任选或全部。",
                    },
                    {
                        "category": "insufficient_evidence",
                        "location": "draft.item.materials[1].evidence",
                        "note": "申请表说明保留申请时提交一份已签字；引文只有并提交部分，未覆盖申请时的先行阶段。",
                    },
                ]
            )
        notes = {
            "M01": "仅报名、材料为空，保留10月18日晚24:00并待确认，不从标题补年。",
            "M02": "两项均展示所有报名者、报名时上传；电子版及一张一寸齐全，均引用完整句。",
            "M03": "报名表全体适用；学生证复印件首次条件、报名阶段、往年豁免均可见；引用完整条件豁免句，与冻结参考一致。",
            "M04": "名称齐全，申请表提交及两证任一原件携带核验均可见；证件引用阶段不足见errors。",
            "M05": "仅领取，现场签署且无需准备的登记表排除，不猜提供方，显示原文未提及。",
            "M06": "下载打印一份、申请时自行携带、现场签署后提交以及完整原句均保留。",
            "M09": "全体报名者一份PDF表；校外实习条件、校内豁免、盖章接收函、报名时上传及跨句依据均保留；排除报到身份证。",
            "M10": "两组均保留，原件及申请表一份已签字可见；排除无需准备的确认单，不推断提供方；缺关系待核对及阶段依据见errors。",
        }
        grades.append(
            {
                "case": run["case"],
                "trial": run["trial"],
                "reviewer": "Codex agent; not independent human acceptance",
                "passed_agent_review": not errors,
                "semantic_review": "scored",
                "checks": {
                    "action": "当前事项范围正确；M01第1轮引用无效见errors",
                    "deadline": "符合冻结时间规则，所有轮次均有匹配依据，无补年份或时刻",
                    "materials": notes[run["case"]],
                    "must_exclude": "已逐项核对，无误收录、扩大事项或新增背景事项",
                    "facts": "已逐项核对expected.facts；剩余错误详见errors",
                },
                "errors": errors,
                "actual": run.get("draft"),
                "raw_model_outputs": [a.get("choices") for a in run["attempts"]],
                "expected": cases[run["case"]]["expected"],
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
        "gate": "FAILED; 17/24, below frozen 24/24 threshold",
        "total": len(grades),
        "passed_agent_review": passed,
        "service_successes": sum(r["outcome"] == "success" for r in raw["runs"]),
        "error_counts_by_trial": dict(counts),
        "http_attempts": raw["http_attempts"],
        "usage": raw["usage"],
        "holdout_status": "M09/M10 evaluated and reviewed; not untouched for future tuning",
        "runs": grades,
    }
    (folder / "issue-12-candidate-v2-grades.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in report.items() if k != "runs"}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
