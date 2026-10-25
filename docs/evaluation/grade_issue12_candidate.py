"""Reproduce the explicit agent review of candidate e51d053's 24 captured trials.

This is a review annotation renderer, not an automatic semantic evaluator.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path


def main():
    folder = Path(__file__).resolve().parent
    raw_bytes = (folder / "issue-12-candidate-raw.json").read_bytes()
    raw = json.loads(raw_bytes)
    cases = json.loads((folder / "issue-12-cases.json").read_text(encoding="utf-8"))
    expected = {case["id"]: case["expected"] for case in cases["cases"]}
    grades = []
    for run in raw["runs"]:
        errors = []

        def add(category, location, note, findings=errors):
            findings.append({"category": category, "location": location, "note": note})

        if run["outcome"] != "success":
            add(
                "execution_failure",
                "items",
                "返回4事项，现有单事项服务拒绝；无草稿，材料语义未评分。",
            )
        else:
            add(
                "insufficient_evidence",
                "draft.item.deadline",
                "本轮模型未提供deadline.evidence；服务标invalid且归回待确认，同时将raw原话覆盖为空。完整年月日样例丢失明确日期；M01丢失截止原话。",
            )
            if run["case"] == "M02":
                add(
                    "qualifier_distortion",
                    "draft.item.materials[0:2].requirement",
                    "上传、报名时、电子版和一张一寸已保留；两份材料均未展示所有报名者条件。",
                )
                add(
                    "insufficient_evidence",
                    "draft.item.materials[0:2].evidence",
                    "仅引用报名时上传部分，缺所有报名者先行条件。",
                )
            if run["case"] == "M03":
                # This complete condition/exemption quote is the frozen reference.
                # Do not add a stronger citation requirement than that annotation.
                if run["trial"] in (2, 3):
                    add(
                        "qualifier_distortion",
                        "draft.item.materials[1].requirement",
                        "保留首次报名和往年豁免，但没有完整表达报名时提交阶段。",
                    )
                if run["trial"] == 3:
                    add(
                        "qualifier_distortion",
                        "draft.item.materials[0].requirement",
                        "报名表说明缺所有报名者。",
                    )
                    add(
                        "insufficient_evidence",
                        "draft.item.materials[0].evidence",
                        "报名表引用缺所有报名者的先行条件。",
                    )
            if run["case"] == "M04":
                add(
                    "insufficient_evidence",
                    "draft.item.materials[1].evidence",
                    "任选原件、携带核验和阶段在说明中完整；引文从并携带或携带开始，未覆盖申请时。",
                )
            if run["case"] == "M07":
                add(
                    "qualifier_distortion",
                    "draft.item.materials[0].requirement",
                    "报名表保留所有报名者及提交责任，未明确报名阶段；条件同意书完整。",
                )
                add(
                    "insufficient_evidence",
                    "draft.item.materials[0].evidence",
                    "仅所有报名者提交报名表，未覆盖前文研学报名阶段。",
                )
        grades.append(
            {
                "case": run["case"],
                "trial": run["trial"],
                "reviewer": "Codex agent; not independent human acceptance",
                "passed_agent_review": not errors,
                "semantic_review": "not_scored" if run["outcome"] != "success" else "scored",
                "checks": {
                    "action": "当前事项同义表达可接受"
                    if run["outcome"] == "success"
                    else "执行失败；原始返回额外资格／检查事项",
                    "deadline": "缺依据，服务保守拒绝且原话被清空"
                    if run["outcome"] == "success"
                    else "无草稿",
                    "materials": "逐项名称、责任、条件、阶段、限定、关系见errors；无材料名称遗漏"
                    if run["outcome"] == "success"
                    else "未评分",
                    "must_exclude": "无误收录"
                    if run["outcome"] == "success"
                    else "无草稿；原始返回误收证件和检查行为，作为原始观察记录",
                    "facts": "逐项对照expected；零材料不表示无需材料；M08保留斜杠并明确关系待核对",
                },
                "errors": errors,
                "actual": run.get("draft"),
                "raw_model_outputs": [a.get("choices") for a in run["attempts"]],
                "expected": expected[run["case"]],
            }
        )
    counts = Counter(c for g in grades for c in {e["category"] for e in g["errors"]})
    report = {
        "candidate_commit": raw["candidate_commit"],
        "prompt_sha256": raw["prompt_sha256"],
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "review_status": "agent_review_complete; independent_human_acceptance_pending",
        "gate": "FAILED; 0/24, do not merge or proceed to issue 4",
        "total": len(grades),
        "passed_agent_review": 0,
        "service_successes": sum(r["outcome"] == "success" for r in raw["runs"]),
        "error_counts_by_trial": dict(counts),
        "http_attempts": raw["http_attempts"],
        "usage": raw["usage"],
        "holdout_status": "M07/M08 evaluated and reviewed; cannot be claimed untouched for a revised candidate",
        "runs": grades,
    }
    (folder / "issue-12-candidate-grades.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in report.items() if k != "runs"}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
