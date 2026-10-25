"""Reproduce explicit agent review of all 24 authorized v4 trials.

Annotations follow reading every raw response and application result, not automatic
semantic grading. Persistence checks replay recorded HTTP in temporary SQLite.
"""

import hashlib
import json
import tempfile
from pathlib import Path

import httpx

from campus_notification_assistant.service import NotificationService


def main():
    folder = Path(__file__).resolve().parent
    raw_bytes = (folder / "issue-12-candidate-v4-raw.json").read_bytes()
    raw = json.loads(raw_bytes)
    cases = {
        c["id"]: c
        for c in json.loads(
            (folder / "issue-12-cases-v4-proposal.json").read_text(encoding="utf-8")
        )["cases"]
    }
    assert raw["status"] == "captured_not_graded"
    assert [(r["case"], r["trial"]) for r in raw["runs"]] == [
        (case, n) for case in cases for n in (1, 2, 3)
    ]
    notes = {
        "M01": "只报名、缺年24:00待确认、材料为空；三轮事项引用均为直接报名请求。第3轮模型误收参赛证件，应用排除。",
        "M02": "所有报名者报名时上传电子表及一张一寸照，两项均需；第2轮补显式主体，第3轮补完整原句；事实及依据均齐全。",
        "M03": "全体交表；首次学生报名时另交学生证复印件，往年免交；第2轮只补表的共同主体，不扩大首次材料范围。条件豁免引用与冻结参考一致。",
        "M04": "申请时交表，另携带学生证或校园卡任一原件核验；single/any及整句阶段依据齐全，不是三者任选。",
        "M05": "仅领取；现场签登记但明确无需准备，不收材料，不猜提供方，不新增无需材料状态。",
        "M06": "提前下载打印一份，申请时自行携带，现场签署后提交，完整过程及原句均保留。",
        "M13": "缺年6月2日前待确认；所有申请者一份PDF表；困难群体证明复印件及其他人豁免、跨句先行条件齐全；入住卡排除。",
        "M14": "领取时自行携带身份证/护照原件到前台核验，斜杠uncertain且可见待核对；另交两张一寸照；完整阶段引文、现场登记表排除均正确。",
    }
    model_issues = {
        ("M01", 3): {
            "category": "false_inclusion_or_misattribution",
            "location": "response.items[0].materials[0]",
            "note": "模型误收参赛备查证件组，应用按阶段排除，未删除原文。",
        },
        ("M02", 2): {
            "category": "qualifier_distortion",
            "location": "response.items[0].materials[*].requirement",
            "note": "模型漏全体主体，应用从无条件共用原句保留所有报名者。",
        },
        ("M03", 2): {
            "category": "qualifier_distortion",
            "location": "response.items[0].materials[0].requirement",
            "note": "模型的报名表说明漏全体主体，应用补回；条件学生证复印件未套用全体。",
        },
        ("M02", 3): {
            "category": "insufficient_evidence",
            "location": "response.items[0].materials[*].evidence",
            "note": "模型引文从报名时上传开始，应用补足原句的共用主体。",
        },
    }
    grades = []
    with tempfile.TemporaryDirectory(prefix="issue12-v4-confirm-") as temporary:
        for run in raw["runs"]:
            assert run["outcome"] == "success" and run["draft"]
            body = {"choices": run["attempts"][-1]["choices"]}
            service = NotificationService(
                Path(temporary) / f"{run['case']}-{run['trial']}.sqlite3",
                transport=httpx.MockTransport(
                    lambda request, body=body: httpx.Response(200, json=body)
                ),
            )
            service.set_source(run["draft"]["source"])
            draft = service.extract(api_key="offline-recorded-response")
            assert draft.item.model_dump(mode="json") == run["draft"]["item"]
            assert draft.item.evidence_ready and service.list_todos() == []
            for field in (draft.item.action, draft.item.deadline, *draft.item.materials):
                if field.evidence:
                    assert draft.source[field.evidence_start : field.evidence_end] == field.evidence
            saved = service.confirm(draft.id)
            assert service.confirm(draft.id).id == saved.id
            restored = NotificationService(service.database).get_todo(saved.id)
            assert restored.item == draft.item and restored.source == draft.source
            issue = model_issues.get((run["case"], run["trial"]))
            grades.append(
                {
                    "case": run["case"],
                    "trial": run["trial"],
                    "reviewer": "Codex agent; independent human semantic acceptance pending",
                    "passed_agent_review": True,
                    "errors": [],
                    "model_raw_passed_agent_review": issue is None,
                    "model_raw_errors": [issue] if issue else [],
                    "checks": {
                        "action": "单一当前事项，动作引用及对象上下文支持，不新增后续背景事项",
                        "deadline": "M01/M13缺年原话待确认，其他完整日期及日期精度正确；不补时刻或借标题补年",
                        "materials": notes[run["case"]],
                        "must_exclude": "按原样例逐项核对排除对象，无遗漏排除项或伪造材料",
                        "evidence": "实际引文逐字匹配、位置正确且语义支持说明；全体主体和跨句条件分别核对",
                        "facts": "逐项核对expected.facts，允许同义措辞，不以JSON完全相等评分",
                    },
                    "recorded_response_save_restart_verified": True,
                    "actual": run["draft"],
                    "expected": cases[run["case"]]["expected"],
                }
            )
    report = {
        "candidate_commit": raw["candidate_commit"],
        "prompt_sha256": raw["prompt_sha256"],
        "suite": raw["suite"],
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "review_status": "agent_review_complete; independent_human_semantic_acceptance_pending",
        "gate": "AGENT_RULESET_MET; 24/24 application drafts; human semantic acceptance pending",
        "total": 24,
        "passed_agent_review": 24,
        "model_raw_passed_agent_review": 20,
        "development_passed": 18,
        "holdout_passed": 6,
        "service_successes": 24,
        "http_attempts": raw["http_attempts"],
        "usage": raw["usage"],
        "recorded_response_save_restart_verified": 24,
        "verification_additional_api_calls": 0,
        "holdout_status": "M13/M14 first called only after freezing 1915eca; now consumed",
        "runs": grades,
    }
    (folder / "issue-12-candidate-v4-grades.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in report.items() if k != "runs"}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
