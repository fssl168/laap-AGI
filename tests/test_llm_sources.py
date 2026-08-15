# -*- coding: utf-8 -*-
"""llm_sources.py LLM 多源 provider 测试（mock urllib/subprocess，不依赖真实服务）。"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from laap.paper_trading import llm_sources as ls


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    ls._refresh_env()
    yield


def test_source_chain_llm_has_new_sources():
    from laap.paper_trading.data_sources import source_chain
    assert "ollama" in source_chain("LLM")
    assert "local" in source_chain("LLM")
    assert "cli" in source_chain("LLM")


def _mock_urlopen(monkeypatch, payload: str):
    import io
    import urllib.request

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return payload.encode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    return _Resp()


def test_ollama_provider(monkeypatch):
    _mock_urlopen(monkeypatch, json.dumps({"message": {"content": "【ollama】"}}))
    out = ls._provider_ollama("q", "s", 100)
    assert out == "【ollama】"


def test_local_provider(monkeypatch):
    _mock_urlopen(monkeypatch, json.dumps(
        {"choices": [{"message": {"content": "【local】"}}]}))
    out = ls._provider_local("q", "s", 100)
    assert out == "【local】"


def test_cli_provider(monkeypatch):
    class _Proc:
        returncode = 0
        stdout = "【cli】"
        stderr = ""

    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    out = ls._provider_cli("q", "s", 100)
    assert out == "【cli】"


def test_build_llm_call_falls_back_on_failure(monkeypatch):
    """首源失败 → 回退下一源。"""
    calls = []

    def _bad(*a, **k):
        calls.append("bad")
        raise ConnectionError("down")

    def _good(*a, **k):
        calls.append("good")
        return "OK"

    monkeypatch.setattr(ls, "_PROVIDERS", {"ollama": _bad, "local": _good})
    monkeypatch.setenv("LLM_SOURCES", "ollama,local")
    call = ls.build_llm_call()
    assert call("p") == "OK"
    assert calls == ["bad", "good"]


def test_build_llm_call_all_fail_raises(monkeypatch):
    def _bad(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(ls, "_PROVIDERS", {"ollama": _bad, "local": _bad})
    monkeypatch.setenv("LLM_SOURCES", "ollama,local")
    call = ls.build_llm_call()
    with pytest.raises(RuntimeError):
        call("p")


def test_anspire_provider(monkeypatch):
    """Anspire OpenAI 兼容网关 provider（mock urlopen）。"""
    import os
    monkeypatch.setenv("ANSPIRE_API_KEYS", "test-key")
    import io, urllib.request, json as _json
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self):
            return _json.dumps(
                {"choices": [{"message": {"content": "【anspire】"}}]}).encode()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    out = ls._provider_anspire("q", "s", 100)
    monkeypatch.delenv("ANSPIRE_API_KEYS", raising=False)
    assert out == "【anspire】"


def test_anspire_requires_key():
    import os
    monkeypatch_env = __import__("pytest").MonkeyPatch()
    monkeypatch_env.delenv("ANSPIRE_API_KEYS", raising=False)
    try:
        with pytest.raises(RuntimeError, match="ANSPIRE_API_KEYS"):
            ls._provider_anspire("q", "s", 100)
    finally:
        monkeypatch_env.undo()
