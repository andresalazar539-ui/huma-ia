# ================================================================
# Testes: flush do buffer espera o processamento anterior (2026-10-10)
#
# Achado no teste de carga de 500 leads: a trava do flush fica presa
# durante toda a resposta (IA + digitação); a mensagem que chegava nesse
# meio-tempo ficava no buffer sem ninguém processar. Agora espera.
# ================================================================

import asyncio
import json

from huma.services import message_buffer as mb


class _FakeRedis:
    """Só o que o _flush_buffer usa: set(nx, ex), lrange, delete."""

    def __init__(self, lists: dict, locked: set):
        self.kv: dict = {k: "1" for k in locked}
        self.lists = lists
        self.deleted: list = []

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def lrange(self, key, a, b):
        return list(self.lists.get(key, []))

    async def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)
            self.lists.pop(k, None)
            self.deleted.append(k)


def _run(coro):
    return asyncio.run(coro)


class TestFlushEspera:

    def test_espera_a_trava_liberar_e_processa_o_que_acumulou(self, monkeypatch):
        lists = {"msgbuf:c1:5511": [json.dumps({"text": "quinta de manhã dá?"}), json.dumps({"text": "ou sexta"})]}
        fake = _FakeRedis(lists, locked={"msgbuf_flush:c1:5511"})
        monkeypatch.setattr(mb, "_client", fake)
        monkeypatch.setattr(mb, "FLUSH_WAIT_STEP_SECONDS", 0.01)
        monkeypatch.setattr(mb, "FLUSH_WAIT_MAX_SECONDS", 2.0)
        processed = []

        async def callback(client_id, phone, text, image, *args):
            processed.append((client_id, phone, text, image))

        async def release_later():
            await asyncio.sleep(0.05)
            await fake.delete("msgbuf_flush:c1:5511")

        async def scenario():
            asyncio.create_task(release_later())
            await mb._flush_buffer("c1", "5511", callback, ())

        _run(scenario())
        assert processed == [("c1", "5511", "quinta de manhã dá? ou sexta", None)]
        assert "msgbuf_flush:c1:5511" not in fake.kv          # liberou no fim

    def test_sem_trava_processa_na_hora(self, monkeypatch):
        lists = {"msgbuf:c1:5511": [json.dumps({"text": "oi"})]}
        fake = _FakeRedis(lists, locked=set())
        monkeypatch.setattr(mb, "_client", fake)
        processed = []

        async def callback(client_id, phone, text, image, *args):
            processed.append(text)

        _run(mb._flush_buffer("c1", "5511", callback, ()))
        assert processed == ["oi"]

    def test_desiste_so_depois_do_teto(self, monkeypatch):
        lists = {"msgbuf:c1:5511": [json.dumps({"text": "oi"})]}
        fake = _FakeRedis(lists, locked={"msgbuf_flush:c1:5511"})
        monkeypatch.setattr(mb, "_client", fake)
        monkeypatch.setattr(mb, "FLUSH_WAIT_STEP_SECONDS", 0.01)
        monkeypatch.setattr(mb, "FLUSH_WAIT_MAX_SECONDS", 0.05)
        processed = []

        async def callback(*a):
            processed.append(a)

        _run(mb._flush_buffer("c1", "5511", callback, ()))
        assert processed == []
        assert lists["msgbuf:c1:5511"]                        # a mensagem continua no buffer pro próximo flush

    def test_trava_do_flush_cobre_a_resposta_inteira(self):
        # 4 partes × 15 s de digitação + IA + juiz cabem em 90 s.
        assert mb.FLUSH_LOCK_TTL_SECONDS >= 90
        assert mb.FLUSH_WAIT_MAX_SECONDS < mb.FLUSH_LOCK_TTL_SECONDS
