"""`tero guide` and `tero notify`: protocol text and relay ping."""

from __future__ import annotations

from typing import Any

from src.errors import scrub_secrets
from src.loop.detach import AGENT_PROTOCOL
from src.relay.iface import RelayClient, make_relay  # noqa: F401 — повторный экспорт


HOSTS_GUIDE = (
    "Как спать и как будить.\n"
    "\n"
    "Claude Code\n"
    "Фоновая команда будит агента, когда завершается: основной путь — "
    "`tero go --detach`, потом `tero wait --messages` фоновой командой. Вызов на переднем плане "
    "длится до 10 мин (по умолчанию 2 мин), фоновый — до 2 ч, потом его убивают и агента "
    "будят. Значит, `tero wait --messages --timeout` держать ниже 7200 с "
    "(берём 6600), код `6` значит «просто запусти `wait --messages` снова». Снаружи сессию можно "
    "разбудить: `claude -p --resume <session-id> \"<текст>\"`; `claude --bg --resume <id>` "
    "продолжает её фоном (`--fork-session` создаёт новый id). Запасной путь — команда "
    "пробуждения:\n"
    "`claude -p --permission-mode acceptEdits \"Ты мозг tero. Выполни: tero guide; "
    "tero status --json --run {run_dir}; ответь на вопрос {question_id}\"`.\n"
    "\n"
    "opencode\n"
    "Будит ли фоновая команда агента — не замерено; пока не замерено, основной путь — "
    "команда пробуждения, а не фоновый `wait`:\n"
    "`opencode run --dangerously-skip-permissions \"Ты мозг tero. Выполни: tero guide; "
    "tero status --json --run {run_dir}; ответь на вопрос {question_id}\"`.\n"
    "\n"
    "Muse\n"
    "Тот же `claude -p` на Anthropic-совместимом API Meta. Если Muse — хозяин внутри "
    "Claude Code, сон и пробуждение такие же, как у Claude Code (предположение, проверить). "
    "Команда пробуждения — команда Claude Code выше, запущенная с окружением Muse "
    "(`ANTHROPIC_BASE_URL=https://api.meta.ai`, ключ из `~/.tero/secrets.env`, "
    "модель `muse-spark-1.3`).\n"
)


def _preset_text(working_dir: str) -> str:
    """Активный пресет и итоговые роли: агент видит их первым делом."""
    try:
        from src.config import resolve_config
        from src.config_preset import load_preset

        cfg = resolve_config({"working_dir": working_dir})
        keys = list(load_preset(cfg.preset)) or ["judge_provider", "judge_model", "player_provider",
                                                 "player_model", "escalation", "escalation_provider",
                                                 "escalation_model", "jev_enabled", "max_debt"]
    except Exception as exc:
        return f"пресет: не прочитан ({type(exc).__name__})\n"
    lines = [f"пресет: {cfg.preset} (итог с yaml/env этого каталога)"]
    lines += [f"  {k}: {getattr(cfg, k)}" for k in keys if hasattr(cfg, k)]
    return "\n".join(lines) + "\n"


def run_guide(args: Any) -> int:
    """Print AGENT_PROTOCOL and the per-host sleep guide. Always code 0."""
    _ = args
    print(_preset_text(getattr(args, "working_dir", None) or "."))
    print(AGENT_PROTOCOL)
    print(HOSTS_GUIDE)
    return 0


def run_notify(args: Any) -> int:
    """Send text via the relay; with NoopRelay there is nowhere to send (code 0)."""
    text = scrub_secrets(getattr(args, "text", "") or "")
    relay = make_relay()
    if relay.notify(text):
        print("уведомление отправлено")
    else:
        skipped = str(getattr(relay, "last_skipped", "") or "")
        why = {"cooldown": "то же уведомление уже уходило за последние 30 мин",
               "no-subscription": "на телефоне не включены пуши",
               "rate": "слишком много уведомлений за 30 мин"}.get(skipped, "")
        print(f"не ушло: {why}" if why else "ретранслятора нет, сообщение не ушло")
    return 0
