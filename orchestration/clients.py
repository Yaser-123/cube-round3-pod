"""How the orchestrator talks to an agent: in-process (Python) or HTTP (any language)."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


class AgentUnavailable(Exception):
    """Connection / 5xx: worth retrying."""


class AgentTimeout(AgentUnavailable):
    """No answer in time: worth retrying."""


class AgentRejected(Exception):
    """4xx: the request or tenancy was refused. Retrying will not help."""


def load_manifest(stage: str) -> dict:
    return json.loads((ROOT / "agents" / stage / "agent.json").read_text())


class InProcClient:
    def __init__(self, manifest: dict):
        self.handle = importlib.import_module(manifest["module"]).handle

    def run(self, request: dict, timeout_s: float) -> dict:  # timeout is not enforced in-process
        try:
            return self.handle(request)
        except LookupError as exc:
            raise AgentRejected(str(exc)) from exc


class HttpClient:
    def __init__(self, manifest: dict):
        env = f"{manifest['stage'].upper()}_URL"
        self.url = os.environ.get(env, manifest["url"]).rstrip("/")

    def run(self, request: dict, timeout_s: float) -> dict:
        try:
            resp = httpx.post(f"{self.url}/run", json=request, timeout=timeout_s)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise AgentUnavailable(f"{type(exc).__name__}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise AgentTimeout(f"{type(exc).__name__}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise AgentUnavailable(f"{type(exc).__name__}: {exc}") from exc
        if 400 <= resp.status_code < 500:
            raise AgentRejected(f"HTTP {resp.status_code}: {resp.text[:300]}")
        if resp.status_code >= 500:
            raise AgentUnavailable(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    def health(self) -> dict:
        return httpx.get(f"{self.url}/health", timeout=5).json()


def client_for(stage: str):
    manifest = load_manifest(stage)
    mode = os.environ.get("ORCH_MODE") or manifest["mode"]
    return InProcClient(manifest) if mode == "inproc" else HttpClient(manifest)
