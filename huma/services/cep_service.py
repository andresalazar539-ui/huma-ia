# ================================================================
# huma/services/cep_service.py — Endereço pelo CEP (ViaCEP)
#
# Checkout de Conversa (2026-09-08): o lead manda só CEP e número; rua,
# bairro, cidade e UF vêm daqui. ViaCEP é público, sem chave, e o
# resultado fica 30 dias no Redis. Nunca levanta.
# ================================================================

from __future__ import annotations

import json
import re

import httpx

from huma.utils.logger import get_logger

log = get_logger("cep_service")

_VIACEP = "https://viacep.com.br/ws/{cep}/json/"
_KEY = "cep:{cep}"
_TTL = 30 * 86400
_TIMEOUT = 6.0


def normalize(cep: str) -> str:
    """"01311-000" → "01311000"; "" se não tem 8 dígitos."""
    digits = re.sub(r"\D", "", str(cep or ""))
    return digits if len(digits) == 8 else ""


def parse_viacep(body: dict) -> dict:
    """Resposta do ViaCEP → {address, neighborhood, city, state}; {} se inválido."""
    if not isinstance(body, dict) or body.get("erro"):
        return {}
    city = str(body.get("localidade") or "").strip()
    state = str(body.get("uf") or "").strip().upper()
    if not city or not state:
        return {}
    return {
        "address": str(body.get("logradouro") or "").strip(),
        "neighborhood": str(body.get("bairro") or "").strip(),
        "city": city,
        "state": state,
    }


async def lookup(cep: str) -> dict:
    """Endereço do CEP ({} se inválido/indisponível). Cache 30 dias."""
    key_cep = normalize(cep)
    if not key_cep:
        return {}
    try:
        from huma.services import redis_service as cache

        cached = await cache.get_value(_KEY.format(cep=key_cep))
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as http:
            resp = await http.get(_VIACEP.format(cep=key_cep))
        data = parse_viacep(resp.json() if resp.status_code == 200 and resp.content else {})
    except httpx.HTTPError as e:
        log.warning(f"CEP | ViaCEP indisponível | cep={key_cep} | {type(e).__name__}: {e}")
        return {}
    except ValueError:
        return {}
    if data:
        try:
            from huma.services import redis_service as cache

            await cache.set_with_ttl(_KEY.format(cep=key_cep), json.dumps(data, ensure_ascii=False), ttl=_TTL)
        except Exception:
            pass
    else:
        log.info(f"CEP | não encontrado | cep={key_cep}")
    return data
