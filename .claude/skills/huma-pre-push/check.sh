#!/usr/bin/env bash
# Verificador pré-push da HUMA. Uso: bash .claude/skills/huma-pre-push/check.sh
# Sai com 1 se algo bloqueante falhou. Avisos não bloqueiam, mas devem ser lidos.
set -u
cd "$(git rev-parse --show-toplevel)" || exit 1
fail=0
warn() { printf '\n[AVISO] %s\n' "$*"; }
bad()  { printf '\n[FALHA] %s\n' "$*"; fail=1; }
ok()   { printf '[ok] %s\n' "$*"; }

changed=$( { git diff --name-only HEAD; git diff --name-only --cached; git ls-files --others --exclude-standard; } | sort -u | grep -v '^$' )
echo "== Arquivos mudados =="; echo "${changed:-<nenhum>}"

# 1. Testes
echo; echo "== pytest =="
if python -m pytest huma/tests/ -q --tb=short; then ok "pytest"; else bad "pytest vermelho"; fi

# 2. Cockpit smoke se houve .jsx
if echo "$changed" | grep -q '^huma/static/cockpit/.*\.jsx$'; then
  echo; echo "== Cockpit smoke (mudou .jsx) =="
  if [ ! -d scripts/cockpit_smoke/node_modules ]; then (cd scripts/cockpit_smoke && npm install --silent); fi
  if (cd scripts/cockpit_smoke && node run.js); then ok "cockpit smoke"; else bad "cockpit smoke vermelho (tela branca em prod)"; fi
else
  ok "sem .jsx mudado, smoke não precisa"
fi

# 3. Travessão em arquivo de produto
src=$(echo "$changed" | grep -E '^huma/.*\.(py|jsx|html|js)$' | grep -v '^huma/tests/' || true)
if [ -n "$src" ]; then
  hits=$(echo "$src" | xargs -r grep -ln $'\xe2\x80\x94' 2>/dev/null || true)
  if [ -n "$hits" ]; then bad "travessão (U+2014) em texto de produto: $hits"; else ok "sem travessão"; fi
  # 4. except vazio
  ex=$(echo "$src" | xargs -r grep -nA1 'except Exception' 2>/dev/null | grep -E '^\S+-[0-9]+-\s*pass\s*$' || true)
  if [ -n "$ex" ]; then bad "except Exception + pass (regra 10):"$'\n'"$ex"; else ok "sem except vazio"; fi
fi

# 5. Campo novo em schema sem migration
if echo "$changed" | grep -q '^huma/models/schemas.py$'; then
  if echo "$changed" | grep -q '^scripts/migration_.*\.sql$'; then
    warn "schemas.py mudou E há migration mudada: confirmar que ela foi APLICADA em prod antes do push"
  else
    warn "schemas.py mudou SEM migration nova. Campo em Conversation/ClientIdentity que vai pro upsert quebra prod inteiro. Ver skill huma-migration."
  fi
fi

# 6. Arquivo sensível sem CLAUDE.md
sens='huma/core/orchestrator.py|huma/services/ai_service.py|huma/services/scheduling_service.py|huma/services/payment_service.py|huma/core/funnel.py|huma/services/conversation_intelligence.py'
if echo "$changed" | grep -Eq "^($sens)$" && ! echo "$changed" | grep -q '^CLAUDE.md$'; then
  warn "arquivo sensível mudou e CLAUDE.md não: se a mudança é estrutural, registrar lá"
fi

echo
if [ "$fail" -eq 0 ]; then python .claude/skills/huma-pre-push/hook.py --stamp; echo "RESULTADO: liberado (leia os avisos)"; else echo "RESULTADO: BLOQUEADO"; fi
exit $fail
