"""Compact of a role: threshold checks, inbox/compact.json requests, snapshots."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.loop import snapshot as snapshot_mod
from src.loop.compact import should_compact
from src.loop.inbox import inbox_dir
from src.loop.phase_ctx import PhaseContext
from src.loop.state import RunState


class CompactStep:
    def __init__(self, ctx: PhaseContext) -> None:
        self.ctx = ctx

    def _player_compact_failed(self) -> None:
        """Раунд после ответа __compacted__ без проверенного ref.

        Гейт и судья не запускаются (F5): работа исполнителя не завершена,
        вердикта нет. Останавливаемся (F5, F9): без проверенного ref бриф
        для нового вызова не построить, следующий раунд без снимка и
        compact-события запрещён. Компакт — не падение и не продолжение
        (F9): счётчик player_crashes не растёт, had_crash не выставляется,
        счётчики K_max не трогаем.
        """
        self.ctx.ledger.event(
            "player_compact_failed",
            round=self.ctx.st.round,
            crashes=self.ctx.st.player_crashes,
        )
        self.ctx.st.stop_reason = "snapshot_failed"
        return None

    def _compact_file(self, role: str) -> Path:
        return self.ctx.run_dir / "inbox" / "compact.json"

    def _take_requested_compact(self, role: str) -> bool:
        """Забрать compact.json текущей роли: requested=True, файл удалить.

        Чужой файл не трогаем. Возвращает True при забранном запросе.
        Забираем атомарно через rename до чтения: замена файла между
        чтением и удалением (запрос другой роли) остаётся на месте
        до вызова этой роли.
        """
        import json
        import os
        import time

        if inbox_dir(self.ctx.run_dir) is None:
            return False
        target = self._compact_file(role)
        claimed = target.parent / f"compact.{os.getpid()}.{time.monotonic_ns()}.claimed"
        try:
            os.rename(target, claimed)
        except OSError:
            return False
        try:
            raw = claimed.read_text(encoding="utf-8")
        except OSError:
            return False
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        if not isinstance(data, dict) or data.get("role") != role:
            # Чужой запрос — кладём обратно, но только в пустой слот:
            # новая запись, появившаяся после нашего rename, новее и
            # её нельзя затирать (last-write-wins).
            try:
                if not target.exists():
                    os.replace(claimed, target)
                else:
                    claimed.unlink()
            except OSError:
                pass
            return False
        self.ctx.update_state(compact_requested=role)
        try:
            claimed.unlink()
        except OSError:
            pass
        return True

    def _claude_autocompacts(self, role: str) -> bool:
        """True, когда роль на claude и её порог задан (> 0).

        Вне диапазона --autocompact CLI (100000–1000000) флаг не
        передаётся (предупреждение в `_autocompact_flag`), но мягкая
        смена tero по порогу тоже отключается — компактит сам CLI.
        """
        from src.providers.claude_native import _positive_int

        if role == "player":
            provider, at_tokens = self.ctx.cfg.player_provider, self.ctx.cfg.compact_player_at_tokens
        else:
            provider, at_tokens = self.ctx.cfg.judge_provider, self.ctx.cfg.compact_judge_at_tokens
        return provider == "claude" and _positive_int(at_tokens) > 0

    def _clear_compact_requested(self, role: str) -> None:
        """Поле очищается, когда стартовал новый вызов роли."""
        try:
            state = RunState.read(self.ctx.run_dir / "state.json") or {}
        except Exception:
            return
        if state.get("compact_requested") == role:
            self.ctx.update_state(compact_requested="")

    def _player_compact_check(self) -> Any:
        at_tokens = self.ctx.cfg.compact_player_at_tokens
        if self._claude_autocompacts("player"):
            # Компактит сам CLI (--autocompact): мягкая смена по порогу
            # для этой роли не включается.
            at_tokens = 0
        if self.ctx.st.compact_count >= 3:
            return None

        def _check(tokens: int) -> bool:
            # Файл может появиться прямо во время вызова: смотрим на
            # каждой границе шага, а не один раз при создании проверки.
            requested = self._take_requested_compact("player")
            return should_compact(tokens, at_tokens=at_tokens, requested=requested)

        return _check

    def _judge_compact_check(self) -> Any:
        at_tokens = self.ctx.cfg.compact_judge_at_tokens
        if self._claude_autocompacts("judge"):
            at_tokens = 0
        if self.ctx.st.judge_compact_used:
            return None

        def _check(tokens: int) -> bool:
            requested = self._take_requested_compact("judge")
            return should_compact(tokens, at_tokens=at_tokens, requested=requested)

        return _check

    def _record_compact(self, role: str, data: dict[str, Any]) -> str | None:
        run_id = self.ctx.ledger.run_dir.name
        if role == "player":
            ref = f"refs/tero/{run_id}/phase-{self.ctx.phase.index:02d}-round{self.ctx.st.round:02d}-compact{self.ctx.st.compact_count}"
        else:
            ref = f"refs/tero/{run_id}/phase-{self.ctx.phase.index:02d}-round{self.ctx.st.round:02d}-judge-compact"
        try:
            snapshot_mod.take_snapshot(self.ctx.repo, self.ctx.checkpoint, ref)
            if not snapshot_mod.verify(self.ctx.repo, ref):
                raise RuntimeError("snapshot verify failed")
        except Exception as exc:
            self.ctx.update_state(snapshot_ref="")
            self.ctx.emit(
                "snapshot_failed",
                ref=ref,
                base=self.ctx.checkpoint,
                role=role,
                error=str(exc) or type(exc).__name__,
                text=f"снимок работы не сделан (`{ref}`)",
            )
            self.ctx.ledger.event(
                "snapshot_failed",
                ref=ref,
                base=self.ctx.checkpoint,
                error=str(exc) or type(exc).__name__,
                round=self.ctx.st.round,
            )
            return None
        tokens = data.get("tokens", 0)
        if role == "player":
            self.ctx.update_state(snapshot_ref=ref, base=self.ctx.checkpoint)
        text = f"компакт {role} ({tokens} токенов) — новый вызов, снимок `{ref}`"
        self.ctx.emit(
            "compact",
            role=role,
            tokens=tokens,
            ref=ref or "",
            text=text,
        )
        self.ctx.ledger.event(
            "compact",
            role=role,
            tokens=tokens,
            ref=ref,
            round=self.ctx.st.round,
            crashes=self.ctx.st.player_crashes,
            kmax_flat=self.ctx.st.kmax_flat,
        )
        print(f"  компакт {role} ({tokens} токенов) — новый вызов", flush=True)
        if ref and role == "player":
            self.ctx.st.resume_ref = ref
            self.ctx.st.resume_base = self.ctx.checkpoint
            self.ctx.st.resume_active = True
        # Компакт — не падение и не раунд: счётчики не трогаем.
        return ref
