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
