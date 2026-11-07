"""
huma/providers/signature: contrato de assinatura digital (ZapSign, Clicksign, D4Sign).

Caso de uso: a IA fechou a venda e emite a action `send_contract`; o motor cria o
documento a partir de um MODELO cadastrado pelo dono, com os dados do lead, e
manda o link de assinatura na conversa. O webhook do provedor avisa quando foi
assinado e o histórico ganha o marker `[CONTRATO ASSINADO ...]`.
"""
from huma.providers.signature.base import SignatureProvider

__all__ = ["SignatureProvider"]
