# ================================================================
# huma/utils/retry.py — Retry exponencial em APIs externas
#
# Sprint 3 / item 10 — chamadas externas (Twilio, MP, ElevenLabs)
# falham com timeout/5xx/429 em pico. Hoje conversa quebra em 1 erro
# transitivo. Retry com backoff resolve.
#
# Design:
#   - Decorator `with_retry` aplicado em funcao async que LEVANTA em erro.
#     Wrapper externo (silent-failure) fica no caller pra manter compat.
#   - is_transient_error: distingue erro retentavel (timeout, 5xx, 429)
#     de permanente (400, 401, 403, 404, 422 — erro nosso, retentar nao
#     adianta).
#   - NUNCA aplicar em ai_service._call_ai — ja tem retry interno proprio.
# ================================================================

import asyncio
import functools
from typing import Awaitable, Callable, TypeVar

import httpx

from huma.utils.logger import get_logger

log = get_logger("retry")

T = TypeVar("T")

# HTTP status que valem retry (transitivos: rate limit, 5xx)
RETRYABLE_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504, 529}


def is_transient_error(exc: BaseException) -> bool:
    """
    Detecta se um erro vale retry.

    Vale retry:
      - Timeout / connection error / read timeout (httpx + asyncio.TimeoutError)
      - HTTP status 408, 425, 429, 500, 502, 503, 504, 529
      - Twilio errors com status nos códigos acima

    NÃO vale retry:
      - 400, 401, 403, 404, 422 — erro do nosso lado (payload inválido,
        auth ruim, recurso não existe). Retentar não muda nada.
      - Erros de programação (TypeError, ValueError, KeyError, etc).
    """
    # Asyncio timeout
    if isinstance(exc, asyncio.TimeoutError):
        return True

    # Httpx errors
    if isinstance(exc, (
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.RemoteProtocolError,
        httpx.PoolTimeout,
    )):
        return True

    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_HTTP_STATUS

    # Twilio SDK errors (TwilioRestException) — checa por module name
    # pra evitar import direto e dependência opcional.
    err_module = type(exc).__module__ or ""
    if "twilio" in err_module:
        status = getattr(exc, "status", None) or getattr(exc, "code", None)
        if isinstance(status, int) and status in RETRYABLE_HTTP_STATUS:
            return True

    # Genérico: nome do tipo contém "timeout" (socket.timeout, etc.)
    err_name = type(exc).__name__.lower()
    if "timeout" in err_name:
        return True

    return False


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 16.0,
    label: str = "",
):
    """
    Decorator: retry exponencial em função async.

    Args:
        max_attempts: total de tentativas (incluindo primeira). Default 3.
        base_delay: atraso inicial em segundos. Dobra a cada tentativa
                    (backoff exponencial: 1s, 2s, 4s, 8s, max=16s).
        max_delay: cap pro atraso entre tentativas.
        label: nome do caller usado em logs (default: nome da função).

    Comportamento:
        - Se função levanta erro transitivo, aguarda e retenta.
        - Se função levanta erro permanente, re-raise imediato (sem aguardar).
        - Se esgotou tentativas, re-raise última exception.

    Uso:
        @with_retry(max_attempts=3, label="twilio_send")
        async def _send_raw(phone, msg):
            return _twilio_client.messages.create(...).sid
    """
    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            tag = label or fn.__name__
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    if not is_transient_error(exc):
                        # Erro permanente — abort imediato
                        raise
                    if attempt >= max_attempts:
                        log.warning(
                            f"retry | {tag} | esgotou {max_attempts} tentativas | "
                            f"{type(exc).__name__}: {exc}"
                        )
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    log.info(
                        f"retry | {tag} | tentativa {attempt}/{max_attempts} falhou "
                        f"({type(exc).__name__}) | aguardando {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
            # Defensivo (loop não deve sair sem return ou raise)
            if last_exc:
                raise last_exc
            raise RuntimeError(f"retry | {tag} | loop terminou sem resultado")
        return wrapper
    return decorator
