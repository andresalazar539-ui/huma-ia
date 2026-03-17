# ================================================================
# huma/utils/logger.py — Logger padronizado
# ================================================================

import logging
import os
import sys

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.getenv("LOG_FORMAT", "text")  # "text" ou "json"


def get_logger(name: str) -> logging.Logger:
    """
    Cria logger padronizado pra um módulo.

    Uso:
        from huma.utils.logger import get_logger
        log = get_logger("payment")
        log.info("Pix criado | valor=R$350")

    Suporta formato JSON pra produção (LOG_FORMAT=json).
    """
    logger = logging.getLogger(f"huma.{name}")

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)

        if LOG_FORMAT == "json":
            import json

            class JSONFormatter(logging.Formatter):
                def format(self, record):
                    return json.dumps({
                        "ts": self.formatTime(record),
                        "level": record.levelname,
                        "module": record.name,
                        "msg": record.getMessage(),
                    })

            handler.setFormatter(JSONFormatter())
        else:
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")
            )

        logger.addHandler(handler)
        logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    return logger
