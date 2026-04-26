# BACKLOG HUMA IA — Pré-GA

Auditoria pré-GA antes do 1º cliente pago, com foco em robustez para 500 usuários simultâneos.

**Regras:**
- Zero regressão
- Validar funcionalmente, não só estruturalmente
- Apenas mudanças que não exijam plataforma externa (Twilio HMAC fora — vai migrar pra Meta)
- Trade-offs reportados honestamente

---

## Status dos sprints

| Sprint | Tema | Status | Commit |
|---|---|---|---|
| 1 | Segurança crítica | ✅ aplicado | `f8d0f8d` |
| 2 | Cache distribuído | ✅ aplicado | `daeaf7f` |
| 3 | Resiliência | ✅ aplicado (10, 11, 16, 17) | `d7da0a0`, `437f34c`, `c4ad9de` |
| 4 | Observabilidade | 🟡 parcial (13, 18, 34) | `4368a15`, `6380ecb`, `a4296db` |
| 5 | Notificações pro dono | ✅ aplicado (20, 21, 22, 23) | `0591c74`, `8408ae4` |
| 6 | Scheduler ativo | ✅ aplicado (19, 24, 28) | `5ad9107`, `10fea47`, `875b5c5`, `78acc20` |
| 7 | Dashboard WhatsApp | ⏳ pendente | — |
| 8 | Memória + handoff | ⏳ pendente | — |
| 9 | Refactor + segurança extra | ⏳ pendente | — |

---

## 🚨 BLOQUEADORES (antes do 1º cliente pago)

- [ ] **1. Assinatura HMAC no webhook Twilio** — pulado (vai migrar pra Meta)
- [x] **2. Assinatura HMAC no webhook Mercado Pago** — Sprint 1
- [x] **3. Mover `_ia_call_counts` pro Redis** — Sprint 2
- [x] **4. Mover `check_conversations._cache` pro Redis** — Sprint 2
- [x] **5. Mover `_client_cache` e `_plan_cache` pro Redis** — Sprint 2
- [x] **6. UPDATE atômico em wallets** — Sprint 1
- [x] **7. Fallback no `message_buffer` quando Redis off** — Sprint 1
- [x] **8. Proteger `/api/playground/activate`** — Sprint 1

---

## ⚠️ PROBLEMAS IMPORTANTES (semanas 1-2)

- [ ] **9. Assinatura Meta Cloud API** — quando migrar do Twilio
- [x] **10. Retry com backoff em chamadas externas** — Sprint 3 (`437f34c`)
- [x] **11. Compressão assíncrona** — Sprint 3 (`c4ad9de`)
- [x] **12. Rate limit agregado por `client_id`** — Sprint 2
- [x] **13. Mascarar dados sensíveis em logs (LGPD)** — Sprint 4 (`4368a15`)
- [x] **14. Restringir CORS `allow_methods`** — Sprint 1
- [x] **15. Handler de erro respeita `HTTPException`** — Sprint 1
- [x] **16. Graceful shutdown** — Sprint 3 (`d7da0a0`)
- [x] **17. Health check profundo** — Sprint 3 (`d7da0a0`)
- [x] **18. Resolver os 8 testes que falham no baseline** — Sprint 4 (`6380ecb`)
- [x] **19. Scheduler ativo de follow-up** — Sprint 6 (`10fea47`)

---

## 🎨 GAPS PRA "UAU"

- [x] **20. Notificação pro dono quando lead agenda** — Sprint 5 (`0591c74`)
- [x] **21. Notificação pro dono quando lead paga** — Sprint 5 (`0591c74`, opt-in adicionado)
- [x] **22. Notificação pro dono quando lead cancela** — Sprint 5 (`0591c74`)
- [x] **23. Notificação pro dono em lead "quente travado"** — Sprint 5/6 (`8408ae4`)
- [x] **24. Lembrete pré-consulta no WhatsApp** — Sprint 6 (`875b5c5`)
- [ ] **25. Dashboard WhatsApp `/stats`** — Sprint 7
- [ ] **26. Comandos `/hoje` e `/semana`** — Sprint 7
- [ ] **27. Action `request_human_takeover`** — Sprint 8
- [x] **28. NPS automático pós-atendimento** — Sprint 6 (`78acc20`)
- [ ] **29. Campos de memória de longo prazo** — Sprint 8 (precisa SQL)
- [ ] **30. Extração de perfil estável por Haiku dedicado** — Sprint 8

---

## 📊 OBSERVABILIDADE

- [ ] **31. Métrica de latência p95 por turn agregada** — **adiado: Railway logs cobrem pré-GA**
- [ ] **32. Dashboard de erros por serviço externo** — **adiado: grep logs cobre pré-GA**
- [x] **33. Alerta de conversa travada há >2h** — Sprint 4/6 (`8408ae4`)
- [x] **34. Detector de loop interno** — Sprint 4 (`a4296db`)

---

## 🏗️ QUALIDADE DE CÓDIGO (refactor)

- [ ] **35. Quebrar `orchestrator.py` (2000+ linhas)** — Sprint 9
- [ ] **36. Migrar Twilio Sandbox → Meta Cloud API** — decisão estratégica
- [ ] **37. Refatorar fakes em `stress_test_heavy.py`** — pós-GA

---

## 🛡️ SEGURANÇA EXTRA

- [ ] **38. Rate limit por IP** — Sprint 9
- [ ] **39. Validação de phone formato +55** — Sprint 9
- [ ] **40. Sanitização de input contra prompt injection** — Sprint 9

---

## Composição dos sprints restantes

**Sprint 3 — Resiliência:** 10, 11, 16, 17
**Sprint 4 — Observabilidade:** 13, 18, 31, 32, 33, 34
**Sprint 5 — Notificações pro dono:** 20, 21, 22, 23
**Sprint 6 — Scheduler ativo:** 19, 24, 28
**Sprint 7 — Dashboard WhatsApp:** 25, 26
**Sprint 8 — Memória + handoff:** 27, 29, 30
**Sprint 9 — Refactor + segurança extra:** 35, 38, 39, 40
