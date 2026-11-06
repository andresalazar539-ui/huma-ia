"""Hook PreToolUse (Bash) da HUMA: bloqueia `git push` sem verificador pré-push aprovado.

Como funciona:
- `check.sh`, ao passar, chama `python hook.py --stamp` e grava em `.claude/.prepush-ok`
  a impressão digital do código testado (tree hash de huma/, scripts/ e requirements.txt).
- No `git push`, este hook recalcula a impressão digital e compara. Igual = libera.
  Diferente ou sem carimbo = bloqueia (exit 2) e explica o que rodar.

Mudança fora dessas pastas (CLAUDE.md, .claude/, memória) não invalida o carimbo.
Commit novo sem mudança de conteúdo também não (a impressão é do conteúdo, não do HEAD).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

PATHS = ["huma", "scripts", "requirements.txt"]
STAMP_REL = os.path.join(".claude", ".prepush-ok")
PUSH_RE = re.compile(r"(^|[\s;&|(])git\s+(-C\s+\S+\s+)?push\b")


def _run(args: list[str], cwd: str, env: dict | None = None) -> str:
    """Roda um comando git e devolve stdout sem espaços nas pontas."""
    out = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def repo_root() -> str:
    """Raiz do repositório a partir da localização deste arquivo."""
    here = os.path.dirname(os.path.abspath(__file__))
    return _run(["git", "rev-parse", "--show-toplevel"], cwd=here)


def fingerprint(root: str) -> str:
    """Tree hash do conteúdo atual (tracked + untracked, respeitando .gitignore) das pastas de código."""
    fd, index = tempfile.mkstemp(prefix="huma-prepush-")
    os.close(fd)
    os.unlink(index)
    env = dict(os.environ, GIT_INDEX_FILE=index)
    try:
        subprocess.run(["git", "add", "-A", "--"] + PATHS, cwd=root, env=env, check=True, capture_output=True)
        tree = _run(["git", "write-tree"], cwd=root, env=env)
    finally:
        if os.path.exists(index):
            os.unlink(index)
    return hashlib.sha1(tree.encode()).hexdigest()


def stamp(root: str) -> None:
    """Grava o carimbo de aprovação (chamado pelo check.sh ao passar)."""
    path = os.path.join(root, STAMP_REL)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(fingerprint(root) + "\n")
    print(f"[ok] carimbo gravado em {STAMP_REL}")


def read_stamp(root: str) -> str:
    """Lê o carimbo gravado; vazio quando não existe."""
    path = os.path.join(root, STAMP_REL)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def main() -> int:
    """Entrada do hook: lê o JSON do stdin, decide se o push pode seguir."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if "--stamp" in sys.argv:
        stamp(repo_root())
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if not PUSH_RE.search(command):
        return 0
    try:
        root = repo_root()
        current = fingerprint(root)
    except (subprocess.CalledProcessError, OSError) as exc:
        sys.stderr.write(f"[huma-pre-push] não consegui calcular a impressão digital: {exc}\n")
        return 0
    saved = read_stamp(root)
    if saved == current:
        return 0
    motivo = "não há carimbo de aprovação" if not saved else "o código mudou depois do último verificador aprovado"
    sys.stderr.write(
        "[huma-pre-push] git push BLOQUEADO: "
        + motivo
        + ".\nRode `bash .claude/skills/huma-pre-push/check.sh` (testes + smoke do Cockpit + regras) "
        "e, passando, o push é liberado sozinho.\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
