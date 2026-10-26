"""Evaluation CLI gates in isolated Git repositories; external access is forbidden."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def evaluation_repo(tmp_path):
    folder = tmp_path / "docs/evaluation"
    folder.mkdir(parents=True)
    original = ROOT / "docs/evaluation"
    for name in (
        "capture_issue12_candidate.py",
        "preflight.py",
        "contract-examples.json",
        "issue-12-cases.json",
        "issue-12-protocol.md",
    ):
        if (original / name).exists():
            shutil.copyfile(original / name, folder / name)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/version.txt").write_text("frozen", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Offline test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            "freeze fixture",
        ],
        cwd=tmp_path,
        check=True,
    )
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tmp_path, text=True).strip()
    return tmp_path, commit


def invoke(repo, commit, *args, mock_run=False):
    # Replace only real credential/HTTP boundaries, even if a gate regresses.
    bootstrap = """
import httpx, runpy, sys
from keyring.backends.Windows import WinVaultKeyring
def forbidden(*args, **kwargs):
    raise AssertionError('FORBIDDEN_EXTERNAL_ACCESS')
WinVaultKeyring.get_password = forbidden
httpx.HTTPTransport = forbidden
sys.argv = sys.argv[1:]
sys.path.insert(0, str(__import__('pathlib').Path(sys.argv[0]).parent))
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    if mock_run:
        bootstrap = bootstrap.replace(
            "sys.argv = sys.argv[1:]",
            """
import json
WinVaultKeyring.get_password = lambda *args: 'offline-fixture-key'
class OfflineHTTP(httpx.BaseTransport):
    def handle_request(self, request):
        payload = {'items': [{'action': {'value': '报名', 'evidence': '请大家积极报名！'},
                              'deadline': None, 'materials': []}]}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
                             'message': {'content': json.dumps(payload)}}]})
httpx.HTTPTransport = OfflineHTTP
sys.argv = sys.argv[1:]
""",
        )
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            "-c",
            bootstrap,
            str(repo / "docs/evaluation/capture_issue12_candidate.py"),
            "--candidate",
            commit,
            *args,
        ],
        cwd=repo.parent,
        check=False,
        env={**os.environ, "CNA_OFFLINE_CHECKS": "0" if mock_run else "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )


def test_run_requires_bound_user_authorization_before_any_external_access(evaluation_repo):
    repo, commit = evaluation_repo
    result = invoke(repo, commit, "--m01-precheck", "--run", "--output", "new-raw.json")
    assert result.returncode != 0
    assert "authorization_required" in result.stderr
    assert "FORBIDDEN_EXTERNAL_ACCESS" not in result.stderr
    assert not (repo / "docs/evaluation/new-raw.json").exists()


def test_authorization_for_a_different_plan_is_rejected(evaluation_repo):
    repo, commit = evaluation_repo
    approval = repo / "approval.json"
    approval.write_text(json.dumps({"approval_reference": "test only", "plan": {}}))
    result = invoke(repo, commit, "--m01-precheck", "--run", "--authorization", str(approval))
    assert "authorization_scope_mismatch" in result.stderr


def test_incomplete_deadline_contract_example_blocks_the_plan(evaluation_repo):
    repo, commit = evaluation_repo
    examples = repo / "docs/evaluation/contract-examples.json"
    examples.write_text(
        json.dumps(
            {
                "examples": [
                    {
                        "items": [
                            {
                                "action": {"value": "报名", "evidence": "请报名"},
                                "deadline": {
                                    "raw": "2028年4月1日前",
                                    "date": "2028-04-01",
                                    "status": "certain",
                                    "precision": "date",
                                },
                                "materials": [],
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    result = invoke(repo, commit, "--m01-precheck")
    assert result.returncode != 0
    assert "contract_invalid" in result.stderr
    assert "deadline.evidence" in result.stderr


def test_formal_plan_rejects_holdouts_with_recorded_attempts(evaluation_repo):
    repo, commit = evaluation_repo
    history = repo / "docs/evaluation/previous-raw.json"
    history.write_text(
        json.dumps({"runs": [{"case": "M07", "trial": 1, "attempts": [{"state": "in_progress"}]}]})
    )
    result = invoke(repo, commit)
    assert result.returncode != 0
    assert "holdout_consumed" in result.stderr


def test_formal_plan_requires_review_and_rejects_known_development_defects(evaluation_repo):
    repo, commit = evaluation_repo
    missing = invoke(repo, commit)
    assert "development_review_required" in missing.stderr
    review = repo / "review.json"
    review.write_text(json.dumps({"candidate_commit": commit, "unresolved_defects": ["bad quote"]}))
    result = invoke(repo, commit, "--development-review", str(review))
    assert "development_defects" in result.stderr


def test_new_untracked_execution_code_blocks_the_plan(evaluation_repo):
    repo, commit = evaluation_repo
    (repo / "src/new_module.py").write_text("# not frozen\n", encoding="utf-8")
    result = invoke(repo, commit, "--m01-precheck")
    assert result.returncode != 0
    assert "candidate_dirty" in result.stderr


def development_review(repo, commit, *, payload=None, passed=True):
    from campus_notification_assistant.deepseek import PROMPT

    payload = payload or {
        "items": [
            {"action": {"value": "报名", "evidence": "请报名。"}, "deadline": None, "materials": []}
        ]
    }
    folder = repo / "docs/evaluation"
    raw = {
        "candidate_commit": commit,
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "status": "captured_not_graded",
        "evaluation_kind": "development_m01_precheck_not_final",
        "runs": [
            {
                "case": "M01",
                "trial": 1,
                "outcome": "success",
                "draft": {"source": "请报名。"},
                "attempts": [{"choices": [{"message": {"content": json.dumps(payload)}}]}],
            }
        ],
    }
    capture = folder / "development-raw.json"
    capture.write_text(json.dumps(raw), encoding="utf-8")
    review = {
        "candidate_commit": commit,
        "prompt_sha256": raw["prompt_sha256"],
        "raw_file": capture.name,
        "raw_lf_sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
        "unresolved_defects": [],
        "runs": [
            {
                "case": "M01",
                "trial": 1,
                "reviewer": "Offline fixture",
                "passed_agent_review": passed,
                "errors": [] if passed else ["bad quote"],
            }
        ],
    }
    path = repo / "development-review.json"
    path.write_text(json.dumps(review), encoding="utf-8")
    return path


def test_valid_formal_plan_is_offline_and_exact_approval_still_cannot_run_in_checks(
    evaluation_repo,
):
    repo, commit = evaluation_repo
    review = development_review(repo, commit)
    result = invoke(repo, commit, "--development-review", str(review))
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    plan = report["execution_plan"]
    assert len(plan["trials"]) == 24
    assert plan["maximum_http_attempts"] == 48
    assert plan["maximum_output_tokens"] == 196608
    assert plan["development_review"]["reviewed_trials"] == 1
    approval = repo / "approval.json"
    approval.write_text(json.dumps({"approval_reference": "Offline fixture only", "plan": plan}))
    blocked = invoke(
        repo, commit, "--development-review", str(review), "--run", "--authorization", str(approval)
    )
    assert "offline_checks" in blocked.stderr
    assert "FORBIDDEN_EXTERNAL_ACCESS" not in blocked.stderr
    assert not (repo / "docs/evaluation/issue-12-candidate-raw.json").exists()


def test_failed_trial_cannot_be_hidden_by_an_empty_known_defect_list(evaluation_repo):
    repo, commit = evaluation_repo
    review = development_review(repo, commit, passed=False)
    result = invoke(repo, commit, "--development-review", str(review))
    assert "development_defects" in result.stderr


def test_actual_development_response_must_meet_the_output_contract(evaluation_repo):
    repo, commit = evaluation_repo
    payload = {
        "items": [
            {
                "action": {"value": "报名", "evidence": "请报名。"},
                "deadline": {
                    "raw": "4月1日",
                    "date": None,
                    "status": "uncertain",
                    "precision": "unknown",
                },
                "materials": [],
            }
        ]
    }
    review = development_review(repo, commit, payload=payload)
    result = invoke(repo, commit, "--development-review", str(review))
    assert "contract_invalid" in result.stderr and "deadline.evidence" in result.stderr


def test_changed_tracked_code_is_not_evaluated_as_the_frozen_candidate(evaluation_repo):
    repo, commit = evaluation_repo
    (repo / "src/version.txt").write_text("edited", encoding="utf-8")
    result = invoke(repo, commit, "--m01-precheck")
    assert "candidate_dirty" in result.stderr


def test_another_failed_capture_for_the_same_candidate_blocks_formal_planning(evaluation_repo):
    repo, commit = evaluation_repo
    review = development_review(repo, commit)
    (repo / "docs/evaluation/other-raw.json").write_text(
        json.dumps(
            {
                "candidate_commit": commit,
                "evaluation_kind": "development_precheck_not_final",
                "runs": [{"case": "M01", "trial": 2, "outcome": "service_error", "attempts": [{}]}],
            }
        )
    )
    result = invoke(repo, commit, "--development-review", str(review))
    assert "development_defects" in result.stderr


def test_suite_for_another_candidate_is_rejected(evaluation_repo):
    repo, commit = evaluation_repo
    folder = repo / "docs/evaluation"
    names = ("issue-12-cases.json", "issue-12-protocol.md")
    suite = {
        "candidate_commit": "0" * 40,
        "corpus": names[0],
        "protocol": names[1],
        "lf_sha256": {
            name: hashlib.sha256((folder / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
            for name in names
        },
    }
    (folder / "suite.json").write_text(json.dumps(suite), encoding="utf-8")
    result = invoke(repo, commit, "--m01-precheck", "--suite", "suite.json")
    assert "suite_candidate_mismatch" in result.stderr


def test_authorized_mock_capture_retains_every_trial_and_cannot_overwrite(evaluation_repo):
    repo, commit = evaluation_repo
    planned = invoke(repo, commit, "--m01-precheck", "--output", "new-raw.json")
    assert planned.returncode == 0, planned.stderr
    approval = repo / "approval.json"
    approval.write_text(
        json.dumps(
            {
                "approval_reference": "Offline test fixture, not real approval",
                "plan": json.loads(planned.stdout)["execution_plan"],
            }
        )
    )
    args = ("--m01-precheck", "--output", "new-raw.json", "--run", "--authorization", str(approval))
    captured = invoke(repo, commit, *args, mock_run=True)
    assert captured.returncode == 0, captured.stderr
    output = repo / "docs/evaluation/new-raw.json"
    before = output.read_bytes()
    report = json.loads(before)
    assert report["status"] == "captured_not_graded"
    assert report["http_attempts"] == 3
    assert len(report["runs"]) == 3
    assert all(r["outcome"] == "success" and r["contract_errors"] == [] for r in report["runs"])
    repeated = invoke(repo, commit, *args, mock_run=True)
    assert repeated.returncode != 0
    assert output.read_bytes() == before
