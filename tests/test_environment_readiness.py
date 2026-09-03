from __future__ import annotations

import httpx

from skytrap.autonomy.browser_verification import BrowserCapabilityStatus, BrowserVerificationProvider
from skytrap.core.capabilities import CapabilityHealth, CapabilityMatrix
from skytrap.models.base import ModelProvider
from skytrap.models.ollama import OllamaHealthStatus, probe_ollama
from skytrap.models.qualification import ModelQualificationSuite, QualificationStatus


class _Response:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("failed", request=httpx.Request("GET", "http://test"), response=httpx.Response(self.status_code))

    def json(self):
        return self.payload


def test_ollama_probe_distinguishes_not_installed(monkeypatch):
    monkeypatch.setattr("skytrap.models.ollama.shutil.which", lambda _: None)
    report = probe_ollama()
    assert report.status == OllamaHealthStatus.NOT_INSTALLED
    assert not report.binary_present


def test_ollama_probe_distinguishes_missing_model(monkeypatch):
    monkeypatch.setattr("skytrap.models.ollama.shutil.which", lambda _: "/usr/bin/ollama")
    replies = iter([_Response({"version": "1.0"}), _Response({"models": []})])
    monkeypatch.setattr("skytrap.models.ollama.httpx.get", lambda *args, **kwargs: next(replies))
    report = probe_ollama(model="rabbit:latest")
    assert report.status == OllamaHealthStatus.MODEL_MISSING
    assert report.api_accessible and not report.model_present


def test_ollama_probe_requires_real_minimal_generation(monkeypatch):
    monkeypatch.setattr("skytrap.models.ollama.shutil.which", lambda _: "/usr/bin/ollama")
    replies = iter([_Response({"version": "1.0"}), _Response({"models": [{"name": "rabbit:latest"}]})])
    monkeypatch.setattr("skytrap.models.ollama.httpx.get", lambda *args, **kwargs: next(replies))
    monkeypatch.setattr("skytrap.models.ollama.httpx.post", lambda *args, **kwargs: _Response({"response": "OK"}))
    report = probe_ollama(model="rabbit:latest")
    assert report.status == OllamaHealthStatus.HEALTHY
    assert report.model_loadable and report.generation_working


class _OfflineProvider(ModelProvider):
    name = "offline"
    engine = "LOCAL"

    def chat(self, messages):
        raise RuntimeError("offline")


def test_model_qualification_never_fabricates_scores_without_replies():
    result = ModelQualificationSuite().run(_OfflineProvider())
    assert result.qualification_status == QualificationStatus.ERROR
    assert result.success_rate is None
    assert result.coding_score is None
    assert not result.qualified
    assert all(not probe.responded for probe in result.probes)


def test_browser_probe_labels_http_fallback_honestly(monkeypatch):
    provider = BrowserVerificationProvider()
    monkeypatch.setattr(provider, "available", lambda: False)
    monkeypatch.setattr("skytrap.autonomy.browser_verification.shutil.which", lambda _: None)
    report = provider.probe()
    assert report.status == BrowserCapabilityStatus.HTTP_ONLY
    assert not report.launch_working


def test_capability_matrix_exposes_unavailable_fallback_to_planner():
    matrix = CapabilityMatrix()
    matrix.record("browser_verification", CapabilityHealth.UNAVAILABLE, "browser missing", "HTTP_ONLY")
    prompt = matrix.planner_prompt()
    assert "UNAVAILABLE" in prompt
    assert "fallback: HTTP_ONLY" in prompt

