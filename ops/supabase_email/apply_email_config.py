"""
Aplica os templates de e-mail (e opcionalmente o SMTP customizado) no Supabase Auth
via Management API — sem precisar clicar no painel.

Uso:
    # Só os templates (remetente continua o padrão do Supabase):
    set SUPABASE_ACCESS_TOKEN=sbp_...
    set SUPABASE_PROJECT_REF=abcdefghijklmnop
    python ops/supabase_email/apply_email_config.py

    # Templates + SMTP customizado (Resend ou outro provedor):
    set SMTP_HOST=smtp.resend.com
    set SMTP_PORT=465
    set SMTP_USER=resend
    set SMTP_PASS=re_...
    set SMTP_ADMIN_EMAIL=no-reply@humaia.com.br
    set SMTP_SENDER_NAME=HUMA IA
    python ops/supabase_email/apply_email_config.py

O token é um Personal Access Token (https://supabase.com/dashboard/account/tokens).
O project ref é o subdominio do SUPABASE_URL (https://<ref>.supabase.co).
"""

import os
import sys
from pathlib import Path

import httpx

API_BASE = "https://api.supabase.com/v1"
TEMPLATES_DIR = Path(__file__).parent

# (arquivo html, campo de conteudo na API, campo de assunto na API, assunto PT-BR)
TEMPLATES: list[tuple[str, str, str, str]] = [
    ("confirmation.html", "mailer_templates_confirmation_content",
     "mailer_subjects_confirmation", "Confirme seu e-mail — HUMA IA"),
    ("recovery.html", "mailer_templates_recovery_content",
     "mailer_subjects_recovery", "Redefinir sua senha — HUMA IA"),
    ("magic_link.html", "mailer_templates_magic_link_content",
     "mailer_subjects_magic_link", "Seu link de acesso — HUMA IA"),
    ("email_change.html", "mailer_templates_email_change_content",
     "mailer_subjects_email_change", "Confirme seu novo e-mail — HUMA IA"),
]

SMTP_ENV_REQUIRED = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_ADMIN_EMAIL")


def build_payload() -> dict:
    """Monta o corpo do PATCH: assuntos + conteúdos dos templates e, se as env vars
    SMTP_* estiverem completas, a configuração de remetente customizado."""
    payload: dict = {}
    for filename, content_field, subject_field, subject in TEMPLATES:
        path = TEMPLATES_DIR / filename
        if not path.exists():
            print(f"ERRO: template não encontrado: {path}")
            sys.exit(1)
        payload[content_field] = path.read_text(encoding="utf-8")
        payload[subject_field] = subject

    smtp_present = [v for v in SMTP_ENV_REQUIRED if os.environ.get(v)]
    if len(smtp_present) == len(SMTP_ENV_REQUIRED):
        payload["smtp_host"] = os.environ["SMTP_HOST"]
        payload["smtp_port"] = os.environ["SMTP_PORT"]
        payload["smtp_user"] = os.environ["SMTP_USER"]
        payload["smtp_pass"] = os.environ["SMTP_PASS"]
        payload["smtp_admin_email"] = os.environ["SMTP_ADMIN_EMAIL"]
        payload["smtp_sender_name"] = os.environ.get("SMTP_SENDER_NAME", "HUMA IA")
        print(f"SMTP customizado incluído | host={payload['smtp_host']} | "
              f"remetente={payload['smtp_admin_email']}")
    elif smtp_present:
        faltando = sorted(set(SMTP_ENV_REQUIRED) - set(smtp_present))
        print(f"AVISO: SMTP parcial — faltam {faltando}. Aplicando SÓ os templates.")
    else:
        print("SMTP não configurado nas env vars — aplicando só os templates.")

    return payload


def main() -> None:
    """Valida credenciais, envia o PATCH e confirma o resultado com um GET."""
    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "")
    ref = os.environ.get("SUPABASE_PROJECT_REF", "")
    if not token or not ref:
        print("ERRO: defina SUPABASE_ACCESS_TOKEN (sbp_...) e SUPABASE_PROJECT_REF.")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"{API_BASE}/projects/{ref}/config/auth"
    payload = build_payload()

    try:
        resp = httpx.patch(url, headers=headers, json=payload, timeout=30)
    except httpx.TimeoutException:
        print("ERRO: timeout falando com a Management API do Supabase. Tente de novo.")
        sys.exit(1)

    if resp.status_code == 401:
        print("ERRO 401: token inválido ou expirado. Gere outro em "
              "https://supabase.com/dashboard/account/tokens")
        sys.exit(1)
    if resp.status_code == 404:
        print(f"ERRO 404: projeto '{ref}' não encontrado. Confira o project ref "
              "(subdomínio do SUPABASE_URL).")
        sys.exit(1)
    if resp.status_code >= 400:
        print(f"ERRO HTTP {resp.status_code}: {resp.text[:500]}")
        sys.exit(1)

    check = httpx.get(url, headers=headers, timeout=30)
    if check.status_code == 200:
        cfg = check.json()
        subj = cfg.get("mailer_subjects_confirmation", "(?)")
        host = cfg.get("smtp_host") or "(padrão Supabase — limitado, trocar antes do lançamento!)"
        print("OK — configuração aplicada.")
        print(f"  Assunto de confirmação: {subj}")
        print(f"  SMTP: {host}")
    else:
        print("PATCH aplicado (2xx), mas a confirmação via GET falhou "
              f"(HTTP {check.status_code}). Confira no painel.")


if __name__ == "__main__":
    main()
