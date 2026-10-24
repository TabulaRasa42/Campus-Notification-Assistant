"""Render the agent's explicit, offline review of the frozen 18 baseline trials.

Judgments below are reviewed annotations, not an automatic semantic grader.
Keep the original capture immutable; independent human acceptance is pending.
"""

import hashlib
import json
from collections import Counter
from pathlib import Path


def main():
    folder = Path(__file__).resolve().parent
    raw_bytes = (folder / "issue-12-baseline-raw.json").read_bytes()
    raw = json.loads(raw_bytes)
    cases = json.loads((folder / "issue-12-cases.json").read_text(encoding="utf-8"))
    expected = {case["id"]: case["expected"] for case in cases["cases"]}
    findings = {
        "M01": [
            [
                (
                    "execution_failure",
                    "items[0].evidence",
                    "额外事项层字段被结构校验拒绝；没有应用草稿，材料语义不评分。",
                )
            ],
            [
                (
                    "unsupported_inference",
                    "raw.items[0].deadline.date",
                    "模型补为2026-10-18；服务归回待确认，无日期补全年份被展示或保存。",
                ),
                (
                    "insufficient_evidence",
                    "draft.item.deadline",
                    "模型提供-1/-1位置，应用标invalid；须修正后才能保存。",
                ),
            ],
            [],
        ],
        "M02": [
            [
                (
                    "qualifier_distortion",
                    "draft.item.materials[0:2].value",
                    "名称齐全；两项均缺所有报名者、报名时上传，证件照缺一张；限定不能只藏在引用中。",
                ),
                (
                    "insufficient_evidence",
                    "draft.item.materials[0:2].evidence",
                    "仅引用材料名称或数量，未覆盖报名上传责任、阶段和适用对象。",
                ),
            ]
        ]
        * 3,
        "M03": [
            [
                (
                    "qualifier_distortion",
                    "draft.item.materials[0:2].value",
                    "名称齐全；学生证复印件未展示仅首次报名及往年已报名者豁免；两项未完整展示报名提交责任。",
                ),
                (
                    "insufficient_evidence",
                    "draft.item.materials[0:2].evidence",
                    "学生证引文未覆盖往年已报名者豁免；报名表引文未覆盖所有报名者的先行条件。",
                ),
            ]
        ]
        * 3,
        "M04": [
            [
                (
                    "qualifier_distortion",
                    "draft.item.materials",
                    "学生证、校园卡拆成两项，丢失任选其一及原件；申请表缺提交、证件缺携带核验及申请阶段。",
                ),
                (
                    "insufficient_evidence",
                    "draft.item.materials",
                    "名称片段未支持选项关系、原件、阶段与责任。",
                ),
            ],
            [
                (
                    "qualifier_distortion",
                    "draft.item.materials",
                    "保留两证任一原件；申请表缺提交、证件缺携带核验，均缺申请时。",
                ),
                (
                    "insufficient_evidence",
                    "draft.item.materials",
                    "引用保留名称及选项关系，但缺提交、携带核验和阶段。",
                ),
            ],
            [
                (
                    "qualifier_distortion",
                    "draft.item.materials",
                    "任选关系与原件含义正确；仍缺提交、携带核验和申请时。",
                ),
                (
                    "insufficient_evidence",
                    "draft.item.materials",
                    "引用缺责任和阶段；另因应用强制value子串，同义改写被标invalid，非模型选项语义错误。",
                ),
            ],
        ],
        "M05": [[], [], []],
        "M06": [
            [
                (
                    "qualifier_distortion",
                    "draft.item.materials[0].value",
                    "名称齐全；未展示提前下载打印一份、申请时自行携带、现场签署后提交。",
                ),
                (
                    "insufficient_evidence",
                    "draft.item.materials[0].evidence",
                    "第1轮只引用下载打印部分，第2/3轮仅名称；未覆盖完整处理过程和阶段。",
                ),
            ]
        ]
        * 3,
    }
    grades = []
    for run in raw["runs"]:
        errors = findings[run["case"]][run["trial"] - 1]
        grades.append(
            {
                "case": run["case"],
                "trial": run["trial"],
                "reviewer": "Codex agent; not independent human acceptance",
                "passed_agent_review": not errors,
                "semantic_review": "not_scored" if run["outcome"] != "success" else "scored",
                "checks": {
                    "action": "仅当前事项，同义名称可接受"
                    if run["outcome"] == "success"
                    else "无草稿",
                    "deadline": "见errors；除M01第2轮外，已返回草稿均符合时间规则",
                    "materials": "见errors；材料名称无漏项；零材料无召回率分母",
                    "must_exclude": "无误收录" if run["outcome"] == "success" else "未评分",
                    "facts": "逐项对照expected；限定、关系及依据不足详见errors",
                },
                "errors": [{"category": c, "location": p, "note": n} for c, p, n in errors],
                "actual": run.get("draft"),
                "raw_model_outputs": [a.get("choices") for a in run["attempts"]],
                "expected": expected[run["case"]],
            }
        )
    counts = Counter(e["category"] for g in grades for e in g["errors"])
    report = {
        "baseline_commit": raw["commit"],
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "capture_hashes": {
            k: raw[k] for k in ("corpus_sha256", "protocol_sha256", "prompt_sha256")
        },
        "review_status": "agent_review_complete; independent_human_acceptance_pending",
        "total": len(grades),
        "passed_agent_review": sum(g["passed_agent_review"] for g in grades),
        "error_counts_by_trial": dict(counts),
        "http_requests": sum(len(r["attempts"]) for r in raw["runs"]),
        "usage": {
            k: sum(a.get("usage", {}).get(k, 0) for r in raw["runs"] for a in r["attempts"])
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
        "runs": grades,
    }
    (folder / "issue-12-baseline-grades.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in report.items() if k != "runs"}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
