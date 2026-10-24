"""Reproduce explicit agent annotations after reading all three authorized M01 trials.

Not an automatic semantic grader. Independent human acceptance remains pending.
Persistence verification replays recorded HTTP responses; it makes no new API calls.
"""

import hashlib
import json
import tempfile
from pathlib import Path

import httpx

from campus_notification_assistant.service import NotificationService


def main():
    folder = Path(__file__).resolve().parent
    raw_bytes = (folder / "issue-12-precheck-v5-raw.json").read_bytes()
    raw = json.loads(raw_bytes)
    corpus = json.loads((folder / "issue-12-cases.json").read_text(encoding="utf-8"))
    expected = next(c["expected"] for c in corpus["cases"] if c["id"] == "M01")
    assert [(r["case"], r["trial"]) for r in raw["runs"]] == [("M01", 1), ("M01", 2), ("M01", 3)]
    grades = []
    with tempfile.TemporaryDirectory(prefix="issue12-reviewed-save-") as temporary:
        for run in raw["runs"]:
            assert run["outcome"] == "success"
            draft = run["draft"]
            response = {"choices": run["attempts"][-1]["choices"]}
            service = NotificationService(
                Path(temporary) / f"trial-{run['trial']}.sqlite3",
                transport=httpx.MockTransport(
                    lambda request, response=response: httpx.Response(200, json=response)
                ),
            )
            service.set_source(draft["source"])
            replayed = service.extract(api_key="offline-recorded-response")
            assert replayed.item.model_dump(mode="json") == draft["item"]
            assert service.list_todos() == []
            saved = service.confirm(replayed.id)
            restored = NotificationService(service.database).get_todo(saved.id)
            assert restored.source == draft["source"]
            assert restored.item == replayed.item
            model_errors = []
            if run["trial"] == 2:
                model_errors.append(
                    {
                        "category": "false_inclusion_or_misattribution",
                        "location": "response.items[0].materials[0:2]",
                        "note": "模型误收参赛备查的学生证和校园卡；应用按原文阶段排除这两项。",
                    }
                )
            grades.append(
                {
                    "case": "M01",
                    "trial": run["trial"],
                    "reviewer": "Codex agent; independent human acceptance pending",
                    "passed_agent_review": True,
                    "errors": [],
                    "model_raw_passed_agent_review": not model_errors,
                    "model_raw_errors": model_errors,
                    "checks": {
                        "action": "只有校运会报名事项；未扩大为整个比赛，无资格或检查待办",
                        "deadline": "保留10月18日晚24:00；缺年待确认、date为空，未采用标题年或比赛日期",
                        "materials": "应用材料为空，不解释为无需材料；第2轮排除模型误收两项，其余原始即为空",
                        "must_exclude": "参赛证件、保证书、体检及心电图报告、资格和背景安排均不在报名材料中",
                        "evidence": "事项及截止引文与原文连续片段及位置相符；第2轮事项引文保留原文双空格",
                    },
                    "save_restart_verified_by_recorded_response": True,
                    "actual": draft,
                    "expected": expected,
                }
            )
    report = {
        "candidate_commit": raw["candidate_commit"],
        "prompt_sha256": raw["prompt_sha256"],
        "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "evaluation_kind": raw["evaluation_kind"],
        "review_status": "agent_review_complete; independent_human_acceptance_pending",
        "passed_agent_review": 3,
        "model_raw_passed_agent_review": 2,
        "total": 3,
        "gate": "M01_ONLY; 3/3 application drafts; not formal 24-trial acceptance",
        "http_attempts": raw["http_attempts"],
        "usage": raw["usage"],
        "runs": grades,
    }
    (folder / "issue-12-precheck-v5-grades.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps({k: v for k, v in report.items() if k != "runs"}, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
