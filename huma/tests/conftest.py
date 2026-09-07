# ================================================================
# huma/tests/conftest.py — Configuração do pytest
# ================================================================

import os
import sys

# Adiciona root do projeto ao path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Seta variáveis de ambiente pra testes (não precisa de serviços reais)
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-fake-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "eyJ-test-fake-key")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")
os.environ.setdefault("ELEVENLABS_API_KEY", "test-key")
os.environ.setdefault("MERCADOPAGO_ACCESS_TOKEN", "")
os.environ.setdefault("SAFE_MODE", "true")

import pytest


@pytest.fixture(autouse=True)
def _no_background_playbook(monkeypatch):
    """
    Regeração automática do playbook (2026-09-07) é fire-and-forget e
    chamaria a IA de verdade. Nos testes vira um gravador: cada teste
    pode inspecionar `playbook_service._scheduled` ou sobrescrever.
    """
    from huma.services import playbook_service

    scheduled: list[tuple[str, str]] = []

    def _record(client_id: str, reason: str) -> None:
        scheduled.append((client_id, reason))

    monkeypatch.setattr(playbook_service, "schedule_regenerate", _record)
    monkeypatch.setattr(playbook_service, "_scheduled", scheduled, raising=False)
    yield scheduled
