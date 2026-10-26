"""Фильтр секретов: всё, что уходит с машины, проходит redact (план 08-2)."""

from __future__ import annotations

import re
from pathlib import Path

_SK = re.compile(r"sk-[A-Za-z0-9_\-]+")
_AKIA = re.compile(r"AKIA[0-9A-Z]{16}")
_BEGIN = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----")
# Границы — по алфавиту, а не \b: \b считает _ частью слова и секреты
# между подчёркиваниями не маскируются. Слеша в границах нет, но и внутри
# токена его нет — пути вида a/b/c не матчится, а токен между слешами — да.
_HEX = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32,}(?![0-9a-fA-F])")
_B64 = re.compile(r"(?<![A-Za-z0-9+=])[A-Za-z0-9+]{40,}={0,2}(?![A-Za-z0-9+=])")

_MASK = "[redacted]"
# Всё похожее на путь — для случая, когда списка secret_paths нет
# (secret_paths недоступны): безопасно, т.к. в заголовке фазы путей быть не должно.
_PATHLIKE = re.compile(r"[^\s]*[\\/][^\s]*")


def _mask_pem(text: str) -> str:
    """-----BEGIN L----- … -----END L----- → маска по совпадающей метке.

    Без совпадающего END маскируем только остаток строки: материал ключа
    на следующих строках непредсказуем, а заглатывание всего хвоста текста
    прячет доброкачественный контент и ломает длину open_findings.
    """

    # Простой цикл вместо sub: конец блока зависит от метки начала.
    out = []
    pos = 0
    for match in _BEGIN.finditer(text):
        if match.start() < pos:
            continue  # BEGIN внутри уже замаскированного блока
        hit = re.compile(r"-----END " + re.escape(match.group(1)) + r"-----").search(
            text, match.end()
        )
        out.append(text[pos : match.start()])
        if hit is None:
            eol = text.find("\n", match.end())
            pos = len(text) if eol < 0 else eol
            out.append(_MASK)
            continue
        out.append(_MASK)
        pos = hit.end()
    out.append(text[pos:])
    return "".join(out)


def redact_strict(text: str) -> str:
    """redact без списка: секреты плюс всё похожее на путь."""
    if not text:
        return text
    return _PATHLIKE.sub(_MASK, redact(text, ()))


def redact(text: str, secret_paths: list[str] | tuple[str, ...]) -> str:
    """Вырезать секреты в любом месте строки; путь из secret_paths → имя файла."""
    if not text:
        return text
    out = _SK.sub(_MASK, text)
    out = _AKIA.sub(_MASK, out)
    out = _mask_pem(out)
    # B64 раньше HEX: иначе HEX съедает A-F-префикс base64-токена и padding остаётся.
    out = _B64.sub(_MASK, out)
    out = _HEX.sub(_MASK, out)
    for path in secret_paths or []:
        if not path:
            continue
        name = Path(path).name
        # Путь целиком или как хвост длинного пути (и корневой `.env` внутри
        # /abs/repo/.env) → только имя файла.
        out = re.sub(r"(?<![\w\-./])(?:[\w\-./]*/)?" + re.escape(path), name, out)
    return out
