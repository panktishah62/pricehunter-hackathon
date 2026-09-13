"""Smoke assertions for Gemini Live deployment wiring.

These are plain config/smoke checks (not property tests) that verify the
deployment plumbing introduced by the gemini-live-call-provider feature:

  * R8.1 - the pipecat-agent declares the Pipecat Google/Gemini extra
    (the `pipecat-ai[...]` install includes the `google` extra).
  * R8.6 - cloudbuild.yaml provisions the Google API key secret to the
    `deploy-pipecat` step's `--update-secrets` (and only that service).
  * R8.4 / R8.8 - the backend exposes the enable / rail / concurrency /
    spacing settings, and those settings feed the resolved gemini_live
    adapter (concurrency clamped >= 1, spacing clamped >= 0.1).

Run from the backend directory:  .venv/bin/python -m pytest test_deploy_wiring.py -q
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


def _repo_root() -> Path:
    """Resolve the `pricing-agent` repo root from this test file location.

    The backend test lives at pricing-agent/pricehunter/backend/, so the
    expected layout is parents[2] == pricing-agent. We verify by walking up
    until we find a directory that contains both `cloudbuild.yaml` and the
    `pricehunter` package, so the test is robust to a shifted parents index.
    """
    here = Path(__file__).resolve()
    # Fast path: the documented index.
    candidate = here.parents[2]
    if (candidate / "cloudbuild.yaml").exists() and (candidate / "pricehunter").is_dir():
        return candidate
    # Fallback: walk up looking for the repo root marker.
    for parent in here.parents:
        if (parent / "cloudbuild.yaml").exists() and (parent / "pricehunter").is_dir():
            return parent
    raise AssertionError(
        f"Could not locate pricing-agent repo root from {here}; "
        "expected an ancestor containing cloudbuild.yaml and pricehunter/."
    )


REPO_ROOT = _repo_root()
CLOUDBUILD_PATH = REPO_ROOT / "cloudbuild.yaml"
REQUIREMENTS_PATH = REPO_ROOT / "pricehunter" / "pipecat-agent" / "requirements.txt"


def test_repo_files_exist():
    """Sanity-check the computed paths before asserting their contents."""
    assert CLOUDBUILD_PATH.exists(), f"missing cloudbuild.yaml at {CLOUDBUILD_PATH}"
    assert REQUIREMENTS_PATH.exists(), f"missing requirements.txt at {REQUIREMENTS_PATH}"


def test_pipecat_requirements_include_google_extra():
    """R8.1 - the pipecat-ai install declares the Google/Gemini extra."""
    text = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    # Find the pipecat-ai line and pull out its bracketed extras list.
    pipecat_line = next(
        (ln for ln in text.splitlines() if ln.strip().startswith("pipecat-ai[")),
        None,
    )
    assert pipecat_line is not None, "no `pipecat-ai[...]` install line in requirements.txt"
    extras = pipecat_line[pipecat_line.index("[") + 1 : pipecat_line.index("]")]
    extras_set = {e.strip() for e in extras.split(",") if e.strip()}
    assert "google" in extras_set, (
        f"`google` extra missing from pipecat-ai extras: {sorted(extras_set)}"
    )


def _deploy_pipecat_step():
    """Return the deploy-pipecat build step from cloudbuild.yaml."""
    doc = yaml.safe_load(CLOUDBUILD_PATH.read_text(encoding="utf-8"))
    steps = doc.get("steps", [])
    step = next((s for s in steps if s.get("id") == "deploy-pipecat"), None)
    assert step is not None, "no `deploy-pipecat` step found in cloudbuild.yaml"
    return step


def test_deploy_pipecat_provisions_google_api_key_secret():
    """R8.6 - the Google API key is provisioned via deploy-pipecat --update-secrets."""
    step = _deploy_pipecat_step()
    args = step.get("args", [])
    secrets_args = [a for a in args if isinstance(a, str) and a.startswith("--update-secrets")]
    assert secrets_args, "deploy-pipecat has no --update-secrets arg"
    assert any("GOOGLE_API_KEY=" in a for a in secrets_args), (
        "GOOGLE_API_KEY not found in deploy-pipecat --update-secrets"
    )


def test_google_api_key_not_added_to_other_services():
    """R8.7 - the Gemini credential is scoped to pipecat-agent only."""
    doc = yaml.safe_load(CLOUDBUILD_PATH.read_text(encoding="utf-8"))
    for step in doc.get("steps", []):
        if step.get("id") == "deploy-pipecat":
            continue
        for arg in step.get("args", []):
            if isinstance(arg, str) and arg.startswith("--update-secrets"):
                assert "GOOGLE_API_KEY=" not in arg, (
                    f"GOOGLE_API_KEY leaked into step {step.get('id')!r} secrets"
                )


def test_backend_exposes_gemini_live_settings():
    """R8.4 / R8.8 - enable / rail / concurrency / spacing settings exist."""
    from app.config import settings

    for attr in (
        "gemini_live_enabled",
        "gemini_live_telephony_provider",
        "gemini_live_max_concurrent_calls",
        "gemini_live_call_spacing_seconds",
    ):
        assert hasattr(settings, attr), f"settings missing `{attr}`"


def test_settings_feed_gemini_live_adapter():
    """R8.4 / R8.8 - the settings flow through to the resolved adapter."""
    from app.services.voice_agent import _voice_provider_adapter

    adapter = _voice_provider_adapter("gemini_live")
    assert adapter.provider_id == "gemini_live"
    assert adapter.concurrency_limit >= 1
    assert adapter.call_spacing_seconds >= 0.1
