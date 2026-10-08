"""End to end: whole workflows, from case to final outcome, persisted and reloaded."""
import collections
import base64
import hashlib
import json
from pathlib import Path

import pytest

from orchestration.orchestrator import apply_override, bundle, discover_inputs, flow_stages, load_flow, resume, run_workflow
from orchestration.store import FileStore, MemoryStore
from shared.utils.schema import errors

ROOT = Path(__file__).resolve().parents[2]
OUTCOMES = {"CLEAN", "CLAIM_RECOMMENDED", "EXCEPTION", "NEEDS_REVIEW", "INCOMPLETE"}
STATUSES = {"PENDING", "IN_PROGRESS", "COMPLETED", "FAILED", "BLOCKED", "RECOVERY_REQUIRED"}


def test_every_sample_workflow_reaches_a_valid_final_state(cases):
    store, tally = MemoryStore(), collections.Counter()
    for case in cases:
        wf = run_workflow(case, store=store)
        assert errors("workflow-state", wf) == [], wf["workflow_id"]
        assert wf["status"] in STATUSES and wf["status"] not in ("PENDING", "IN_PROGRESS")
        fo = wf["final_outcome"]
        assert fo["outcome"] in OUTCOMES and fo["contributing_records"], "a final outcome must cite its evidence"
        assert fo["provisional"] == (wf["status"] != "COMPLETED")
        for rid in wf["evidence_references"]:
            assert errors("evidence", store.get_evidence(rid)) == []
        tally[(wf["status"], fo["outcome"])] += 1
    assert sum(tally.values()) == len(cases) == 100


def test_audit_trail_explains_every_stage(cases):
    wf = run_workflow(cases[1])
    stages = {t["stage"] for t in wf["transitions"] if t["stage"]}
    assert {s["stage"] for s in wf["stage_results"]} <= stages
    assert wf["transitions"][0]["event"] == "workflow_created"
    assert any(t["event"] == "status_changed" and t["to_status"] == wf["status"] for t in wf["transitions"])


def test_claim_names_amount_and_cites_evidence(cases):
    if "prep" not in flow_stages():
        pytest.skip("Specialist flow has no Prep evidence, so the sample contains no claimable charge")
    store = MemoryStore()
    for case in cases:
        wf = run_workflow(case, store=store)
        if wf["final_outcome"]["outcome"] == "CLAIM_RECOMMENDED":
            assert wf["final_outcome"]["claimable_usd"] > 0
            rec = store.get_evidence(next(s["record_id"] for s in wf["stage_results"] if s["stage"] == "recovery"))
            claimed = [c for c in rec["payload"]["charges"] if c["position"] == "CONTRADICTS"]
            assert claimed and all(c["evidence_record_ids"] for c in claimed), "no claim without attached evidence"
            return
    raise AssertionError("sample data should contain at least one claim")


def test_wrong_tenant_cannot_pull_another_orgs_subject(cases):
    case = cases[0]
    other = "org_demo_bravo" if case["org_id"] == "org_demo_alpha" else "org_demo_alpha"
    wf = run_workflow({**case, "org_id": other})
    receiving = next(s for s in wf["stage_results"] if s["stage"] == "receiving")
    assert receiving["state"] == "error" and receiving["error"]["code"] == "agent_rejected"
    assert wf["status"] == "FAILED"


def test_state_and_evidence_survive_a_restart(tmp_path, cases):
    """Workflow state + evidence are persisted; a new process (new store object) can inspect, override and resume."""
    case = next(c for c in cases if c["route"] == "fba")
    wf = run_workflow(case, store=FileStore(tmp_path))
    reloaded = FileStore(tmp_path)
    again = reloaded.load_workflow(wf["workflow_id"])
    assert again == wf and errors("workflow-state", again) == []
    assert bundle(again, reloaded)["evidence"].keys() == set(wf["evidence_references"])
    rid = wf["evidence_references"][0]
    out = apply_override(wf["workflow_id"], reloaded, record_id=rid, new_verdict="PASS", actor="op_test", reason="restart test")
    assert out["overrides"][0]["supersedes"]["record_id"] == rid
    assert resume(wf["workflow_id"], load_flow(), reloaded)["workflow_id"] == wf["workflow_id"]


def test_captures_in_data_input_become_content_addressed_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("INPUT_DIR", str(tmp_path))
    folder = tmp_path / "UNIT-0001" / "receiving"
    folder.mkdir(parents=True)
    (folder / "carton.jpg").write_bytes(b"not really a jpeg")
    (folder / ".gitkeep").write_text("")
    [item] = discover_inputs("UNIT-0001", "receiving")
    assert item["ref"] == "UNIT-0001/receiving/carton.jpg" and item["kind"] == "image" and len(item["sha256"]) == 64
    assert discover_inputs("UNIT-0001", "prep") == []


def test_pack_capture_includes_base64_without_changing_content_address(tmp_path, monkeypatch):
    monkeypatch.setenv("INPUT_DIR", str(tmp_path))
    folder = tmp_path / "UNIT-0006" / "pack"
    folder.mkdir(parents=True)
    image = b"\x89PNG\r\n\x1a\n"
    (folder / "open_box.png").write_bytes(image)

    [item] = discover_inputs("UNIT-0006", "pack")

    assert item["ref"] == "UNIT-0006/pack/open_box.png"
    assert item["kind"] == "image"
    assert item["sha256"] == hashlib.sha256(image).hexdigest()
    assert item["data_base64"] == base64.b64encode(image).decode("ascii")


def test_matches_expected_outcomes_for_the_organiser_stubs(cases):
    """Golden file for the STUBS + standard flow. Skipped once you replace a stub or change the flow: write your own."""
    manifests = [json.loads((ROOT / "agents" / s / "agent.json").read_text()) for s in ("receiving", "prep", "pack", "returns", "recovery")]
    if any(m["implementation"] != "organiser-stub" for m in manifests) or "prep" not in flow_stages():
        pytest.skip("not the stock stubs + standard flow")
    expected = json.loads((ROOT / "data/expected/final-outcomes.sample.json").read_text())
    store = MemoryStore()
    actual = {}
    for case in cases:
        wf = run_workflow(case, store=store)
        fo = wf["final_outcome"]
        actual[wf["workflow_id"]] = {"status": wf["status"], "outcome": fo["outcome"], "needs_human": fo["needs_human"],
                                     "claimable_usd": fo["claimable_usd"]}
    assert actual == expected
