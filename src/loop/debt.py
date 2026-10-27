"""Шип с долгом (план 08-11): открытые находки — в долг, затем путь SHIP.

Долг: `state.json` → `debt: [{phase, round, finding_id, level, problem, why}]`
(машине; страница читает его) и `debt.md` (человеку). Запись — через `fsio`
(`RunState.update` пишет `atomic_write_json`, `debt.md` — `atomic_write_text`).
Прямой коммит мимо `_ship` запрещён: `should_ship` пускает дальше только при
зелёном гейте, красный — раунд продолжается как обычно.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.fsio import atomic_write_text
from src.loop.state import RunState

WHYS = ("polish", "opus", "skip")


def build_entries(
    *,
    phase: int,
    round_no: int,
    findings: list[Any],
    why: str,
) -> list[dict[str, Any]]:
    """Одна запись долга на открытую находку."""
    entries: list[dict[str, Any]] = []
    for item in findings or []:
        if isinstance(item, dict):
            fid = item.get("id")
            level = item.get("level")
            problem = item.get("problem", "")
        else:
            fid = getattr(item, "id", "")
            level = getattr(item, "level", "") or getattr(item, "severity", "")
            if level == "blocking":
                level = getattr(item, "note", "") or level
            problem = getattr(item, "problem", "")
        entries.append(
            {
                "phase": int(phase),
                "round": int(round_no),
                "finding_id": str(fid or ""),
                "level": str(level or ""),
                "problem": str(problem or ""),
                "why": why,
            }
        )
    return entries


def append_debt(run_dir: str | Path, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Дописать долг в `state.json` и `debt.md`.

    Оба артефакта проверяются: `state.json` перечитывается после записи
    (`RunState.update` глотает ошибки записи — молчаливый шип запрещён),
    сбой `debt.md` откатывает `state.json` к прежнему долгу. Любой сбой —
    исключение, вызывающий шиповать не должен.
    """
    from src.errors import scrub_data as _scrub_data
    from src.errors import scrub_secrets as _scrub
    from src.fsio import atomic_write_json

    path = Path(run_dir) / "state.json"
    try:
        current = RunState.read(path) or {}
    except Exception:
        current = {}
    prev = list(current.get("debt") or [])
    debt = prev + list(entries)
    want = _scrub_data(debt)
    RunState(path).update(debt=debt)
    saved = RunState.read(path) or {}
    if list(saved.get("debt") or [])[-len(want):] != want:
        raise IOError(f"debt not persisted: {path}")
    md_path = Path(run_dir) / "debt.md"
    try:
        try:
            old = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
        except Exception:
            old = ""
        lines = [old] if old else ["# Долг фаз\n"]
        for e in entries:
            lines.append(
                f"- [фаза {e.get('phase')} · r{e.get('round')} · {e.get('why')}] "
                f"{e.get('problem')} ({e.get('finding_id') or 'без id'}, {e.get('level') or 'без уровня'})\n"
            )
        atomic_write_text(md_path, _scrub("".join(lines)))
    except Exception:
        try:
            cur = RunState.read(path) or {}
            cur["debt"] = prev
            atomic_write_json(path, _scrub_data(cur), ensure_ascii=False, mode=0o600)
        except Exception:
            pass
        raise
    return debt


def should_ship(*, gate_ok: bool | None) -> bool:
    """Зелёный гейт — можно идти путём SHIP; красный — раунд продолжается."""
    return bool(gate_ok)


def commit_pending(ctx: Any, st: Any) -> None:
    """Записать отложенный долг после зелёного гейта. Сбой — исключение (не шип).

    Пункт убирается из `debt_pending` только после `verify_debt` на его же
    исходных (нескрэбленных) записях — не только когда `append_debt` не
    бросил исключение (F15: скрэб может молча разойтись с тем, что должно
    было уйти в долг, и тогда readback state.json/debt.md совпадёт сам с
    собой, но не с тем, что owed; предыдущий SHIP не должен терять пункт
    из debt_pending в таком случае).
    """
    pending = list(getattr(st, "debt_pending", None) or [])
    if not pending:
        return
    try:
        round_no = int(getattr(st, "round", 0) or 0)
    except (TypeError, ValueError):
        round_no = 0
    try:
        title = str(getattr(getattr(ctx, "phase", None), "title", "") or "")
    except Exception:
        title = ""
    for pos, item in enumerate(pending):
        entries = list((item or {}).get("entries") or [])
        if not entries:
            continue
        try:
            append_debt(ctx.run_dir, entries)
            if not verify_debt(ctx.run_dir, entries):
                raise IOError(f"debt not verified: {ctx.run_dir}")
        except Exception:
            # F10/F15: уже верифицированные пункты не повторяются при
            # ретрае, а неверифицированные (включая этот) остаются в
            # debt_pending — следующий SHIP их допишет, а не пропустит
            # долг мимо state/debt.md.
            try:
                st.debt_pending = [p for p in list(pending[pos:]) if (p or {}).get("entries")]
            except Exception:
                pass
            raise
        try:
            ctx.ledger.event("debt", reason=str((item or {}).get("why") or ""),
                             count=len(entries), round=round_no,
                             role="judge", phase=title)
            ctx.emit("debt", reason=str((item or {}).get("why") or ""),
                     count=len(entries), round=round_no,
                     role="judge", text=f"долг: {len(entries)}")
        except Exception:
            pass
    try:
        st.debt_pending = []
    except Exception:
        pass


def pending_debt_entries(ctx: Any, raw_level: dict[str, Any] | None, why: str) -> list[dict[str, Any]]:
    """Записи долга без записи: коммит в state/debt.md — только после зелёного гейта (F4)."""
    st = getattr(ctx, "st", None)
    items = list(getattr(st, "open_findings", None) or [])
    rows: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            f, p = str(item.get("file", "")), str(item.get("problem", ""))
            rows.append({"id": item.get("id"), "level": item.get("level"),
                         "problem": item.get("problem", "")})
        else:
            f, p = str(getattr(item, "file", "")), str(getattr(item, "problem", ""))
            level = getattr(item, "level", "") or (raw_level or {}).get(f + "\x00" + p)
            rows.append({"id": getattr(item, "id", ""), "level": level,
                         "problem": getattr(item, "problem", "")})
    try:
        phase_index = int(getattr(getattr(ctx, "phase", None), "index", 0) or 0)
    except (TypeError, ValueError):
        phase_index = 0
    try:
        round_no = int(getattr(st, "round", 0) or 0)
    except (TypeError, ValueError):
        round_no = 0
    return build_entries(phase=phase_index, round_no=round_no, findings=rows, why=why)


def verify_debt(run_dir: str | Path, entries: list[dict[str, Any]]) -> bool:
    """Оба артефакта содержат записи этого шипа. False — шип запрещён.

    Пустые entries — обычный SHIP без долга: проверять нечего. Сверять весь
    долг прогона нельзя: долг skip живёт только в state.json, и такая
    сверка запрещала бы любой следующий шип — фаза крутила бы раунды вечно.
    Невписанный долг не теряется: commit_pending оставляет его в debt_pending.
    """
    if not entries:
        return True
    try:
        state = RunState.read(Path(run_dir) / "state.json") or {}
    except Exception:
        return False
    debt = list(state.get("debt") or [])
    tail = debt[-len(entries):] if len(debt) >= len(entries) else []
    if tail != entries:
        return False
    try:
        md = (Path(run_dir) / "debt.md").read_text(encoding="utf-8")
    except Exception:
        return False
    return all(str(e.get("problem") or "") in md for e in entries)


def ship_open_findings(ctx: Any, raw_level: dict[str, Any] | None, why: str) -> list[dict[str, Any]]:
    """Открытые находки фазы — в долг + события debt. Возвращает записи.

    Сбой записи — исключение: вызывающий идёт в обычный раунд, не в SHIP (F6).
    """
    entries = pending_debt_entries(ctx, raw_level, why)
    try:
        st = getattr(ctx, "st", None)
        round_no = int(getattr(st, "round", 0) or 0)
    except (TypeError, ValueError):
        round_no = 0
    append_debt(ctx.run_dir, entries)
    if not verify_debt(ctx.run_dir, entries):
        raise IOError(f"debt not verified: {ctx.run_dir}")
    try:
        phase_title = str(getattr(getattr(ctx, "phase", None), "title", "") or "")
        ctx.ledger.event("debt", reason=why, count=len(entries), round=round_no,
                         role="judge", phase=phase_title)
        ctx.emit("debt", reason=why, count=len(entries), round=round_no,
                 role="judge", text=f"долг: {len(entries)} ({why})")
    except Exception:
        pass
    return entries
