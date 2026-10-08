"""Starter orchestrator: a small workflow engine that owns workflow state.

Model:  Agent Output -> Evidence Record (stored, immutable) -> state transition -> next stage -> ... -> Final Outcome.
The orchestrator is the authoritative owner of workflow state. Agents return evidence; they never write state.

What it does for you (keep or replace, but keep the behaviour; it is tested):
  * start_workflow / advance / resume / apply_override
  * routes stages from a JSON flow (`when`), passes ALL previous evidence and overrides to each agent
  * validates every agent output (schema, stage, workflow, tenant, hash, consistency) before accepting it
  * retries transient failures, never retries refusals; every failure is RECORDED, never hidden or turned into success
  * fails open: a broken agent becomes a pending/error evidence record and the workflow continues (or blocks, by policy)
  * keeps an audit trail (`transitions`) and derives status + final outcome from the evidence (rollup.py)
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path

from shared.utils.hashing import verify
from shared.utils.log import get_logger
from shared.utils.records import error_obj, pending_output, utcnow
from shared.utils.schema import errors as schema_errors

from agents.pack.input_resolver import MAX_IMAGE_BYTES
from .clients import AgentRejected, AgentTimeout, AgentUnavailable, client_for, load_manifest
from .rollup import derive_final_outcome, derive_status, effective
from .store import MemoryStore

ROOT = Path(__file__).resolve().parents[1]
logger = get_logger("orchestrator")
KINDS = {".jpg": "image", ".jpeg": "image", ".png": "image", ".webp": "image", ".heic": "image",
         ".mp4": "video", ".mov": "video", ".pdf": "document", ".csv": "document", ".json": "document", ".txt": "document"}


# ---------------------------------------------------------------- flow
def default_flow_path() -> Path:
    """The flow named in pod.json (Specialist Pods run flow.specialist.json), else flow.json."""
    pod = ROOT / "pod.json"
    rel = json.loads(pod.read_text()).get("flow") if pod.exists() else None
    return ROOT / (rel or "orchestration/flow.json")


def load_flow(path: str | Path | None = None) -> dict:
    return json.loads(Path(path or default_flow_path()).read_text())


def flow_stages(flow: dict | None = None) -> list[str]:
    return list(dict.fromkeys(s["stage"] for s in (flow or load_flow())["steps"]))


def applies(step: dict, case: dict) -> tuple[bool, str]:
    for key, allowed in step.get("when", {}).items():
        if case.get(key) not in allowed:
            return False, f"{key}={case.get(key)!r} not in {allowed}"
    return True, ""


def discover_inputs(subject_id: str, stage: str) -> list[dict]:
    """Captures for one stage live in data/input/<subject_id>/<stage>/ (override the root with INPUT_DIR).

    Each file becomes a content-addressed input {ref, kind, sha256}. Refs are relative to the input root:
    never absolute (no local paths in evidence).
    """
    root = Path(os.environ.get("INPUT_DIR", ROOT / "data" / "input"))
    folder = root / subject_id / stage
    if not folder.is_dir():
        return []
    inputs = []
    for path in sorted(folder.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue

        ref = path.relative_to(root).as_posix()
        kind = KINDS.get(path.suffix.lower(), "other")
        if stage == "pack" and kind == "image" and path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError(f"Pack image exceeds the {MAX_IMAGE_BYTES}-byte limit: {ref}")

        image = path.read_bytes()
        item = {"ref": ref, "kind": kind, "sha256": hashlib.sha256(image).hexdigest()}
        if stage == "pack" and kind == "image":
            if len(image) > MAX_IMAGE_BYTES:
                raise ValueError(f"Pack image exceeds the {MAX_IMAGE_BYTES}-byte limit: {ref}")
            item["data_base64"] = base64.b64encode(image).decode("ascii")
        inputs.append(item)
    return inputs


# ---------------------------------------------------------------- workflow state
def workflow_id_for(case: dict) -> str:
    return f"WF-{case['org_id']}-{case.get('subject_id') or case['unit_id']}"


def _log(wf: dict, event: str, stage: str | None = None, detail: str | None = None, **extra) -> None:
    wf["transitions"].append({"at": utcnow(), "event": event, "stage": stage, "detail": detail, **extra})
    logger.info(event, extra={"ctx": {"workflow_id": wf["workflow_id"], "org_id": wf["org_id"],
                                     "subject_id": wf["subject_id"], "stage": stage, "detail": detail}})


def _set_status(wf: dict, status: str, reason: str) -> None:
    if wf["status"] != status:
        _log(wf, "status_changed", detail=reason, from_status=wf["status"], to_status=status)
    wf["status"], wf["status_reason"] = status, reason
    wf["timestamps"]["updated_at"] = utcnow()


def new_workflow(case: dict, flow: dict) -> dict:
    """A PENDING workflow with every stage listed and routing already decided."""
    now = utcnow()
    subject_id = case.get("subject_id") or case["unit_id"]
    stage_results = []
    for step in flow["steps"]:
        ok, why = applies(step, case)
        try:
            agent_id = load_manifest(step["stage"])["agent_id"]
        except FileNotFoundError:
            agent_id = None
        stage_results.append({
            "stage": step["stage"], "agent_id": agent_id, "state": "pending" if ok else "skipped",
            "skipped_reason": None if ok else why, "record_id": None, "evidence_status": None, "verdict": None,
            "outcome": None, "needs_human": None, "next_step_recommendation": None, "runs": 0, "attempts": 0,
            "started_at": None, "finished_at": None, "duration_ms": None, "error": None})
    wf = {"schema_version": "1.0", "workflow_id": workflow_id_for(case), "flow_id": flow["flow_id"],
          "org_id": case["org_id"], "subject_id": subject_id,
          "context": {k: v for k, v in case.items() if k not in ("org_id", "unit_id", "subject_id")},
          "status": "PENDING", "status_reason": "created", "current_stage": None, "previous_stage": None,
          "stage_results": stage_results, "evidence_references": [],
          "timestamps": {"created_at": now, "updated_at": now, "completed_at": None},
          "errors": [], "overrides": [], "halted": None, "final_outcome": None, "transitions": []}
    _log(wf, "workflow_created", detail=f"flow={flow['flow_id']}")
    for sr in stage_results:
        if sr["state"] == "skipped":
            _log(wf, "stage_skipped", sr["stage"], sr["skipped_reason"])
    return wf


def _previous_evidence(wf: dict, upto: int, store) -> list[dict]:
    out = []
    for sr in wf["stage_results"][:upto]:
        if sr["record_id"] and (rec := store.get_evidence(sr["record_id"])):
            out.append(rec)
    return out


def _validate(out: dict, wf: dict, stage: str) -> list[str]:
    """Why an agent output is not acceptable (empty list = accept)."""
    bad = schema_errors("agent-output", out)
    if bad:
        return bad[:3]
    ev = out["evidence"]
    if out["stage"] != stage or ev["stage"] != stage:
        return [f"stage mismatch: expected {stage!r}, got {out['stage']!r}/{ev['stage']!r}"]
    if out["workflow_id"] != wf["workflow_id"] or ev["workflow_id"] != wf["workflow_id"]:
        return ["workflow_id mismatch"]
    if (ev["subject"]["org_id"], ev["subject"]["subject_id"]) != (wf["org_id"], wf["subject_id"]):
        return ["TENANCY/SUBJECT MISMATCH: evidence is about a different org or subject than this workflow"]
    if not verify(ev):
        return ["content_hash does not match the evidence body"]
    if (out["verdict"], out["status"], out["agent_id"]) != (ev["decision"]["verdict"], ev["status"], ev["agent_id"]):
        return ["output and evidence disagree (verdict/status/agent_id)"]
    return []


def _run_stage(wf: dict, sr: dict, idx: int, opts: dict, store, client) -> dict | None:
    """Run one stage. Returns a halt reason, or None. Always leaves a stored evidence record behind."""
    stage = sr["stage"]
    sr["runs"] += 1
    sr["attempts"], sr["started_at"], sr["error"] = 0, utcnow(), None
    wf["previous_stage"], wf["current_stage"] = wf["current_stage"], stage
    base = f"{wf['workflow_id']}:{stage}"
    request = {
        "schema_version": "1.0", "request_id": base if sr["runs"] == 1 else f"{base}:r{sr['runs']}",
        "workflow_id": wf["workflow_id"], "stage": stage,
        "subject": {"org_id": wf["org_id"], "subject_id": wf["subject_id"], "route": wf["context"].get("route", "unknown")},
        "inputs": [],
        "previous_evidence": _previous_evidence(wf, idx, store),
        "context": {"overrides": wf["overrides"], "case": wf["context"]},
    }
    t0, out, err = time.monotonic(), None, None
    input_discovery_failed = False
    try:
        request["inputs"] = discover_inputs(wf["subject_id"], stage)
    except ValueError as exc:
        input_discovery_failed = True
        err = error_obj("invalid_input", str(exc), retryable=False, stage=stage)

    while sr["attempts"] <= int(opts["retries"]):
        if input_discovery_failed:
            break
        sr["attempts"] += 1
        try:
            out = client.run(request, float(opts["timeout_s"]))
            err = None
            break
        except AgentTimeout as exc:
            err = error_obj("agent_timeout", str(exc), retryable=True, stage=stage)
        except AgentUnavailable as exc:
            err = error_obj("agent_unavailable", str(exc), retryable=True, stage=stage)
        except AgentRejected as exc:
            err = error_obj("agent_rejected", str(exc), retryable=False, stage=stage)
            break
        except Exception as exc:  # an agent bug must not take the orchestrator down
            err = error_obj("agent_exception", f"{type(exc).__name__}: {exc}", retryable=False, stage=stage)
            break
        if sr["attempts"] <= int(opts["retries"]):
            _log(wf, "retry", stage, err["message"])
    if out is not None:
        bad = _validate(out, wf, stage)
        if bad:
            code = "tenant_mismatch" if bad[0].startswith("TENANCY") else "invalid_output"
            err, out = error_obj(code, "; ".join(bad), retryable=False, stage=stage), None
            _log(wf, "invalid_output", stage, err["message"])
    if out is None:
        out = pending_output(request, code=err["code"], message=err["message"], retryable=err["retryable"],
                             agent_id=sr["agent_id"])
        _log(wf, "stage_degraded", stage, f"{err['code']}: recorded as {out['evidence']['status']}; flow policy decides what next")

    ev = out["evidence"]
    store.put_evidence(ev)
    if ev["record_id"] not in wf["evidence_references"]:
        wf["evidence_references"].append(ev["record_id"])
    agent_err = ev.get("error") or err
    if agent_err:
        wf["errors"].append({**agent_err, "stage": stage, "agent_id": ev["agent_id"], "at": agent_err.get("at") or utcnow()})
    sr.update({
        "agent_id": ev["agent_id"], "record_id": ev["record_id"], "evidence_status": ev["status"],
        "verdict": ev["decision"]["verdict"], "outcome": ev["decision"]["outcome"],
        "needs_human": ev["decision"].get("needs_human"), "next_step_recommendation": out.get("next_step_recommendation"),
        "state": "completed" if ev["status"] == "completed" else "error", "error": agent_err,
        "finished_at": utcnow(), "duration_ms": int((time.monotonic() - t0) * 1000)})
    _log(wf, "stage_completed" if sr["state"] == "completed" else "stage_error", stage,
         f"{ev['decision']['outcome']} / {ev['decision']['verdict']}")
    if sr["state"] == "error" and opts["on_error"] == "block":
        return f"stage {stage} failed and on_error=block"
    # Block only when an UNCERTAIN result actually asks for a person. Recovery's SILENT ("no evidence, so no claim")
    # is UNCERTAIN with needs_human=false: there is nothing for a human to decide, so it must not halt the workflow.
    if ev["decision"]["verdict"] == "UNCERTAIN" and ev["decision"].get("needs_human") and opts["on_uncertain"] == "block":
        return f"stage {stage} is UNCERTAIN, needs a person, and on_uncertain=block"
    return None


def _finalize(wf: dict, store) -> dict:
    evidence = {rid: store.get_evidence(rid) for rid in wf["evidence_references"]}
    status, reason = derive_status(wf, evidence)
    _set_status(wf, status, reason)
    wf["final_outcome"] = derive_final_outcome(wf, evidence, status)
    wf["timestamps"]["completed_at"] = utcnow() if status == "COMPLETED" else None
    store.save_workflow(wf)
    return wf


# ---------------------------------------------------------------- public API
def advance(wf: dict, flow: dict, store, clients: dict | None = None) -> dict:
    """Run every stage that has not completed (errored stages are retried), in order, until done or halted."""
    defaults = {"timeout_s": 30, "retries": 1, "on_uncertain": "continue", "on_error": "continue", **flow.get("defaults", {})}
    steps = {s["stage"]: s for s in flow["steps"]}
    wf["halted"] = None
    _set_status(wf, "IN_PROGRESS", "advancing")
    store.save_workflow(wf)
    for idx, sr in enumerate(wf["stage_results"]):
        if sr["state"] in ("completed", "skipped"):
            continue
        step = steps[sr["stage"]]
        opts = {**defaults, **{k: v for k, v in step.items() if k not in ("stage", "when")}}
        client = (clients or {}).get(sr["stage"]) or client_for(sr["stage"])
        halt = _run_stage(wf, sr, idx, opts, store, client)
        store.save_workflow(wf)
        if halt:
            wf["halted"] = {"stage": sr["stage"], "reason": halt, "at": utcnow()}
            _log(wf, "halted", sr["stage"], halt)
            break
    return _finalize(wf, store)


def run_workflow(case: dict, flow: dict | None = None, store=None, clients: dict | None = None) -> dict:
    """Start (or continue) the workflow for a case. Idempotent: an existing workflow is advanced, not duplicated."""
    flow, store = flow or load_flow(), store or MemoryStore()
    wf = store.load_workflow(workflow_id_for(case)) or new_workflow(case, flow)
    return advance(wf, flow, store, clients)


def resume(workflow_id: str, flow: dict | None = None, store=None, clients: dict | None = None) -> dict:
    """Continue after a halt, a person's decision, or a failure (errored stages are retried)."""
    flow = flow or load_flow()
    wf = store.load_workflow(workflow_id)
    if wf is None:
        raise KeyError(workflow_id)
    _log(wf, "resumed", detail=f"from status {wf['status']}")
    return advance(wf, flow, store, clients)


def apply_override(workflow_id: str, store, *, record_id: str, new_verdict: str, actor: str, reason: str,
                   new_outcome: str | None = None) -> dict:
    """A person (or rule) changes the effective decision of a record. Nothing is deleted or rewritten:
    the new entry references the evidence and the previous effective decision, and the state is re-derived."""
    if not actor.strip() or not reason.strip():
        raise ValueError("an override needs an actor and a reason")
    wf = store.load_workflow(workflow_id)
    if wf is None:
        raise KeyError(workflow_id)
    if record_id not in wf["evidence_references"]:
        raise ValueError(f"{record_id} is not evidence in {workflow_id}")
    record = store.get_evidence(record_id)
    previous_verdict, _ = effective(wf, record)
    earlier = [o for o in wf["overrides"] if o["supersedes"]["record_id"] == record_id]
    entry = {"override_id": f"OVR-{len(wf['overrides']) + 1:03d}",
             "supersedes": {"record_id": record_id, "override_id": earlier[-1]["override_id"] if earlier else None},
             "target": "decision", "actor": actor, "at": utcnow(), "reason": reason,
             "original_verdict": record["decision"]["verdict"], "previous_verdict": previous_verdict,
             "new_verdict": new_verdict, "new_outcome": new_outcome}
    wf["overrides"].append(entry)
    _log(wf, "override", record["stage"], f"{entry['override_id']} by {actor}: {previous_verdict} -> {new_verdict}")
    return _finalize(wf, store)


def bundle(wf: dict, store) -> dict:
    """The workflow plus every evidence record it references: a self-contained, reviewable export."""
    return {"workflow": wf, "evidence": {rid: store.get_evidence(rid) for rid in wf["evidence_references"]}}
