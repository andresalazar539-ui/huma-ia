# ================================================================
# huma/utils/log_masking.py — Mascaramento de dados sensíveis em logs
#
# Sprint 4 / item 13 — LGPD compliance.
#
# Funções deterministicas, sem dependências, seguras pra import em qualquer
# módulo. Sempre retornam string. Lidam com None, tipos errados e strings
# curtas sem levantar exceção.
#
# Filosofia: log debugável + privacidade. Mantém prefixo/sufixo identificáveis
# pra correlacionar incidentes, esconde o miolo.
# ================================================================


def mask_email(email: str | None) -> str:
    """
    Mascara email mantendo primeira letra do local e domínio inteiro.

    'camila.silva@gmail.com' → 'c***@gmail.com'
    'a@b.com' → 'a***@b.com'
    '' / None / inválido → '' (caller decide se loga vazio)
    """
    if not email or not isinstance(email, str) or "@" not in email:
        return ""
    local, _, domain = email.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


def mask_name(name: str | None) -> str:
    """
    Mascara nome mantendo primeiro nome + iniciais dos sobrenomes.

    'Camila Silva Santos' → 'Camila S. S.'
    'Camila' → 'Camila'
    '' / None → ''
    """
    if not name or not isinstance(name, str):
        return ""
    parts = [p for p in name.strip().split() if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    initials = " ".join(f"{p[0]}." for p in parts[1:])
    return f"{parts[0]} {initials}"


def mask_cpf(cpf: str | None) -> str:
    """
    Mascara CPF mantendo só os 2 últimos dígitos (verificadores).

    '12345678990' → '***.***.***-90'
    '123.456.789-90' → '***.***.***-90'
    Qualquer string com <2 dígitos → '***'
    """
    if not cpf or not isinstance(cpf, str):
        return ""
    digits = "".join(c for c in cpf if c.isdigit())
    if len(digits) < 2:
        return "***"
    return f"***.***.***-{digits[-2:]}"


def mask_phone(phone: str | None) -> str:
    """
    Mascara telefone mantendo DDD + últimos 4 dígitos.

    '5511999998888' → '5511*****8888'
    '11999998888' → '11*****8888'
    Telefone curto (<7 dígitos) → '***' (não dá pra correlacionar mesmo)
    """
    if not phone or not isinstance(phone, str):
        return ""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) < 7:
        return "***"
    # Mantém os 2-4 primeiros (país+DDD) e últimos 4
    if len(digits) >= 12:  # com país (55) + DDD (2) + número
        prefix = digits[:4]
    elif len(digits) >= 10:  # DDD + número
        prefix = digits[:2]
    else:
        prefix = digits[:2]
    return f"{prefix}*****{digits[-4:]}"
