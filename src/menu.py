"""Interactive TUI settings menu for tero go (spec §6.2)."""

import dataclasses
from pathlib import Path
import yaml

from src.config import Config, short_model_name
from src.providers.effort import (
    EFFORT_LABELS,
    effort_field_for,
    normalize_effort,
    provider_supports_effort,
)

try:
    import questionary

    QUESTIONARY_AVAILABLE = True
except ImportError:
    QUESTIONARY_AVAILABLE = False

# Codex model IDs (OpenAI GPT-5.6 family + GPT-6 Astra).
# Per-role effort from the menu overrides the Codex factory default of "max".
CODEX_MODEL_PRESETS = {
    "GPT-6 Astra": "gpt-6-astra",
    "GPT-5.6 Sol": "gpt-5.6-sol",
    "GPT-5.6 Terra": "gpt-5.6-terra",
    "GPT-5.6 Luna": "gpt-5.6-luna",
    "Default (~/.codex/config.toml)": "",
}

# OpenCode model IDs
OPENCODE_MODEL_PRESETS = {
    "Muse Spark 1.3 (free $0)": "opencode/muse-spark-1.3-contributor-free",
    "MiniMax M2.5 (free)": "opencode/minimax-m2.5-free",
    "MIMO Pro (free)": "opencode/mimo-v2-pro-free",
    "MIMO Omni (free)": "opencode/mimo-v2-omni-free",
    "Kimi K2 (free)": "openrouter/moonshotai/kimi-k2:free",
}

GEMINI_MODEL_PRESETS = {
    "Gemini 3.1 Pro": "gemini-3.1-pro-preview",
    "Gemini 3.8 Flash": "gemini-3.8-flash",
    "Gemini 3.5 Flash-Lite": "gemini-3.5-flash-lite",
}

MUSE_MODEL_PRESETS = {
    "Muse Spark 1.3 (свой ключ)": "muse-spark-1.3",
    "Muse Spark 1.2 (свой ключ)": "muse-spark-1.2",
}

CURSOR_MODEL_PRESETS = {
    "Composer 2.5": "composer-2.5",
    "Grok 4.6": "cursor-grok-4.6",
    "Composer 2": "cursor-2",
}


def _tests_label(config: Config) -> str:
    cmd = (config.test_command or "").strip()
    return cmd if cmd else "автодетект"


def _plan_label(config: Config) -> str:
    name = config.plan_file
    plan_path = Path(config.working_dir).expanduser() / name
    if not plan_path.is_file():
        return f"{name}  (не найден)"
    try:
        from src.loop.plan import load_phases

        phases = load_phases(plan_path)
    except (OSError, ValueError):
        return name
    done = sum(1 for phase in phases if phase.done)
    return f"{name}  (фаз {len(phases)}, готово {done})"


# Provider choices
PROVIDER_PRESETS = {
    "Muse API (свой ключ)": "muse",
    "Claude Pro (native)": "claude",
    "Codex (native CLI)": "codex",
    "Gemini (Google CLI)": "gemini",
    "OpenCode (MIMO/Kimi)": "opencode",
    "Cursor Headless": "cursor",
}

# Claude model choices (for native provider). Full ids so the picker
# does not silently drift when CLI aliases retarget.
CLAUDE_MODEL_PRESETS = {
    "Fable 5.1": "claude-fable-5-1",
    "Opus 5": "claude-opus-5",
    "Sonnet 5": "claude-sonnet-5",
    "Haiku 4.5": "claude-haiku-4-5",
}

FALLBACK_PROVIDER_PRESETS = {
    "Отключить escalation": "",
    **PROVIDER_PRESETS,
}


def _provider_model_label(
    provider: str, model: str, default_text: str = "по умолчанию", effort: str = ""
) -> str:
    """Render provider/model pair for compact menu display."""
    if not provider:
        return "выкл"
    name = short_model_name(model) if model else default_text
    if effort:
        name = f"{name} {effort}"
    return f"{provider} ({name})"


def _model_presets_for_provider(provider: str) -> dict[str, str]:
    """Return menu presets for a selectable provider."""
    if provider == "claude":
        return CLAUDE_MODEL_PRESETS
    if provider == "codex":
        return CODEX_MODEL_PRESETS
    if provider == "opencode":
        return OPENCODE_MODEL_PRESETS
    if provider == "gemini":
        return GEMINI_MODEL_PRESETS
    if provider == "muse":
        return MUSE_MODEL_PRESETS
    if provider == "cursor":
        return CURSOR_MODEL_PRESETS
    return {}


def _custom_model_allowed(provider: str) -> bool:
    """Return True when the provider picker supports manual model entry."""
    return provider in {"codex", "opencode", "cursor"}


def _resolve_model_choice(
    config: Config,
    provider: str,
    model_field: str,
    model_choice: str | None,
) -> Config:
    """Resolve a selected model label into the stored model ID."""
    if not model_choice:
        return config

    presets = _model_presets_for_provider(provider)
    model_id = presets[model_choice]
    if model_id == "__custom__":
        if not _custom_model_allowed(provider):
            return config
        model_id = questionary.text("Введи model ID:").ask()
        if not model_id:
            return config
    return dataclasses.replace(config, **{model_field: model_id})


def _questionary_select_provider_model(
    config: Config,
    provider_field: str,
    model_field: str,
    prompt_label: str,
    provider_choices: dict[str, str] | None = None,
) -> Config:
    """Shared questionary provider/model picker.

    Works with *any* pair of ``<role>_provider`` / ``<role>_model`` field names
    on Config — ``getattr`` + ``dataclasses.replace`` make the function agnostic
    to the specific field, so it supports player, judge, tester, escalation
    without special-casing.
    """
    choices = list((provider_choices or PROVIDER_PRESETS).keys())
    current = getattr(config, provider_field)
    choice = questionary.select(
        f"Провайдер для {prompt_label} (текущий: {current or 'выкл'}):",
        choices=choices,
    ).ask()
    if not choice:
        return config

    provider = (provider_choices or PROVIDER_PRESETS)[choice]
    switched = provider != current
    next_model = getattr(config, model_field) if not switched else ""
    updates: dict = {
        provider_field: provider,
        model_field: "" if not provider else next_model,
    }
    effort_field = effort_field_for(model_field)
    if effort_field:
        updates[effort_field] = (
            "" if switched or not provider else getattr(config, effort_field, "")
        )
    config = dataclasses.replace(config, **updates)

    if not provider:
        return config

    model_choice = questionary.select(
        f"Модель для {prompt_label}:",
        choices=list(_model_presets_for_provider(provider).keys()),
    ).ask()
    if not model_choice:
        return config
    config = _resolve_model_choice(config, provider, model_field, model_choice)
    return _apply_effort_choice(config, provider, model_field)


def _apply_effort_choice(config: Config, provider: str, model_field: str) -> Config:
    """Third picker window: thinking/effort, skipped for Gemini."""
    effort_field = effort_field_for(model_field)
    if not effort_field:
        return config
    if not provider_supports_effort(provider):
        return dataclasses.replace(config, **{effort_field: ""})
    choice = questionary.select(
        f"Effort ({', '.join(EFFORT_LABELS.values())}):",
        choices=list(EFFORT_LABELS.values()),
    ).ask()
    if not choice:
        return dataclasses.replace(config, **{effort_field: ""})
    level = next((key for key, label in EFFORT_LABELS.items() if label == choice), "")
    return dataclasses.replace(config, **{effort_field: level})


def _fallback_prompt_model(provider: str, prompt_label: str) -> str:
    """Prompt for a provider-specific model in the plain-text fallback menu."""
    if provider == "claude":
        print("  Модели: fable, opus, sonnet, haiku")
        model = (
            input(f"  {prompt_label} model [sonnet]: ").strip().lower() or "sonnet"
        )
        return {
            "fable": "claude-fable-5-1",
            "opus": "claude-opus-5",
            "sonnet": "claude-sonnet-5",
            "haiku": "claude-haiku-4-5",
        }.get(model, model)

    if provider == "codex":
        print("  Модели: default, gpt-6-astra, gpt-5.6-sol, gpt-5.6-terra, gpt-5.6-luna")
        model = input(f"  {prompt_label} model [default]: ").strip()
        return "" if model.lower() == "default" else model

    if provider == "opencode":
        print(
            "  Модели: spark-free, mimo-pro, mimo-omni, minimax-m2.5, kimi-k2"
        )
        model = (
            input(f"  {prompt_label} model [mimo-pro]: ").strip().lower() or "mimo-pro"
        )
        model_map = {
            "spark-1.3-free": "opencode/muse-spark-1.3-contributor-free",
            "spark-free": "opencode/muse-spark-1.3-contributor-free",
            "mimo-pro": "opencode/mimo-v2-pro-free",
            "mimo-omni": "opencode/mimo-v2-omni-free",
            "minimax-m2.5": "opencode/minimax-m2.5-free",
            "kimi-k2": "openrouter/moonshotai/kimi-k2:free",
        }
        return model_map.get(model, model)

    if provider == "gemini":
        print("  Модели: gemini-3.1-pro-preview, gemini-3.8-flash, gemini-3.5-flash-lite")
        model = (
            input(f"  {prompt_label} model [gemini-3.1-pro-preview]: ").strip().lower()
            or "gemini-3.1-pro-preview"
        )
        return model

    if provider == "muse":
        print("  Модели: muse-spark-1.3 (свой ключ), muse-spark-1.2")
        return (
            input(f"  {prompt_label} model [muse-spark-1.3]: ").strip().lower()
            or "muse-spark-1.3"
        )

    if provider == "cursor":
        print("  Модели: composer-2.5, grok-4.6, composer-2")
        raw = (
            input(f"  {prompt_label} model [composer-2.5]: ").strip().lower()
            or "composer-2.5"
        )
        return {"grok-4.6": "cursor-grok-4.6", "grok": "cursor-grok-4.6"}.get(raw, raw)

    return ""


def _fallback_select_provider_model(
    config: Config,
    provider_field: str,
    model_field: str,
    prompt_label: str,
    provider_choices: dict[str, str] | None = None,
    empty_value_label: str = "off",
) -> Config:
    """Shared provider/model picker for the plain-text fallback menu."""
    choice_map = provider_choices or PROVIDER_PRESETS
    allowed_values = list(dict.fromkeys(choice_map.values()))
    rendered_values = [value or empty_value_label for value in allowed_values]
    current = getattr(config, provider_field) or (
        empty_value_label if "" in allowed_values else ""
    )

    print(f"  Провайдеры: {', '.join(rendered_values)}")
    raw_value = input(f"  {prompt_label} provider [{current}]: ").strip().lower()
    if not raw_value:
        return config

    if raw_value == empty_value_label.lower() and "" in allowed_values:
        provider = ""
    elif raw_value in allowed_values:
        provider = raw_value
    else:
        return config

    config = dataclasses.replace(config, **{provider_field: provider})
    if not provider:
        cleared = {model_field: ""}
        effort_field = effort_field_for(model_field)
        if effort_field:
            cleared[effort_field] = ""
        return dataclasses.replace(config, **cleared)

    model = _fallback_prompt_model(provider, prompt_label)
    updates = {model_field: model}
    effort_field = effort_field_for(model_field)
    if effort_field:
        updates[effort_field] = _fallback_prompt_effort(provider)
    return dataclasses.replace(config, **updates)


def _fallback_prompt_effort(provider: str) -> str:
    if not provider_supports_effort(provider):
        return ""
    print("  Effort: low, medium, high, xhigh, max")
    raw = input("  effort [high]: ").strip().lower() or "high"
    return normalize_effort(raw)


def _role_display(config: Config, provider_field: str, model_field: str) -> str:
    provider = getattr(config, provider_field)
    model = getattr(config, model_field)
    effort = getattr(config, effort_field_for(model_field), "") or ""
    return _provider_model_label(provider, model, effort=effort)


def run_settings_menu(config: Config) -> Config | None:
    """Show interactive settings menu. Returns updated config or None if user quit.

    Falls back to plain input if questionary is not installed.
    """
    if not QUESTIONARY_AVAILABLE:
        return _fallback_menu(config)
    return _questionary_menu(config)


def _questionary_menu(config: Config) -> Config | None:
    """Arrow-key driven settings menu using questionary."""
    import questionary

    while True:
        choices = [
            questionary.Choice("▶   Запустить", value="start"),
            questionary.Separator("─── роли ───"),
            questionary.Choice(
                f"    Player:      {_role_display(config, 'player_provider', 'player_model')}",
                value="player_provider",
            ),
            questionary.Choice(
                f"    Judge:       {_role_display(config, 'judge_provider', 'judge_model')}",
                value="judge_provider",
            ),
            questionary.Choice(
                f"    Tester:      {_role_display(config, 'tester_provider', 'tester_model')}",
                value="tester_provider",
            ),
            questionary.Choice(
                f"    Escalation:  {_role_display(config, 'escalation_provider', 'escalation_model')}",
                value="escalation_provider",
            ),
            questionary.Separator("─── план ───"),
            questionary.Choice(
                f"    Файл плана:  {_plan_label(config)}", value="plan_file"
            ),
            questionary.Choice(
                f"    Тесты:       {_tests_label(config)}",
                value="test_command",
            ),
            questionary.Separator("───"),
            questionary.Choice("✗   Выход", value="quit"),
        ]

        answer = questionary.select(
            "⚙  tero — настройка  (↑↓ выбор, Enter)",
            choices=choices,
            use_shortcuts=False,
        ).ask()

        if answer is None or answer == "quit":
            return None
        if answer == "start":
            try:
                _save_global_default(config)
            except OSError:
                pass
            return config

        config = _edit_setting_questionary(config, answer)


def _edit_setting_questionary(config: Config, setting: str) -> Config:
    """Edit a single setting using questionary prompts."""
    if setting == "player_provider":
        config = _questionary_select_provider_model(
            config, "player_provider", "player_model", "player"
        )

    elif setting == "judge_provider":
        config = _questionary_select_provider_model(
            config,
            "judge_provider",
            "judge_model",
            "Judge",
        )

    elif setting == "tester_provider":
        config = _questionary_select_provider_model(
            config,
            "tester_provider",
            "tester_model",
            "Tester",
        )

    elif setting == "escalation_provider":
        config = _questionary_select_provider_model(
            config,
            "escalation_provider",
            "escalation_model",
            "escalation",
            provider_choices=FALLBACK_PROVIDER_PRESETS,
        )

    elif setting == "plan_file":
        val = questionary.text(f"Файл плана (текущий: {config.plan_file}):").ask()
        if val:
            config = dataclasses.replace(config, plan_file=val)

    elif setting == "test_command":
        val = questionary.text(
            f"Команда тестов (пусто = автодетект, текущая: {_tests_label(config)}):"
        ).ask()
        if val is not None:
            config = dataclasses.replace(config, test_command=val.strip())

    return config


def _save_global_default(config: Config) -> None:
    """Save current settings to ~/.g3/config.yaml as global defaults."""
    global_config_path = Path.home() / ".g3" / "config.yaml"
    global_config_path.parent.mkdir(parents=True, exist_ok=True)

    data = {"defaults": dataclasses.asdict(config)}
    global_config_path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False))
    wd = config.working_dir.replace(str(Path.home()), "~")
    print(f"\n  Сохранено в ~/.g3/config.yaml (рабочая папка: {wd})\n")


def _fallback_menu(config: Config) -> Config | None:
    """Plain text fallback when questionary is not installed."""
    print("\n⚙  tero — настройка")
    print("  (установи questionary для красивого меню: pip install questionary)\n")

    while True:
        print(f"  [p] Player:      {_role_display(config, 'player_provider', 'player_model')}")
        print(f"  [j] Judge:       {_role_display(config, 'judge_provider', 'judge_model')}")
        print(f"  [t] Tester:      {_role_display(config, 'tester_provider', 'tester_model')}")
        print(f"  [e] Escalation:  {_role_display(config, 'escalation_provider', 'escalation_model')}")
        print(f"  [1] Файл плана:  {_plan_label(config)}")
        print(f"  [s] Тесты:       {_tests_label(config)}")
        print("  [Enter] Запустить (сохраняет настройки)")
        print("  [q] Выход\n")

        answer = input("  › ").strip().lower()

        if answer == "":
            try:
                _save_global_default(config)
            except OSError:
                pass
            return config
        if answer == "q":
            return None
        elif answer == "p":
            config = _fallback_select_provider_model(
                config, "player_provider", "player_model", "Player"
            )
        elif answer == "j":
            config = _fallback_select_provider_model(
                config, "judge_provider", "judge_model", "Judge"
            )
        elif answer == "t":
            config = _fallback_select_provider_model(
                config, "tester_provider", "tester_model", "Tester"
            )
        elif answer == "e":
            config = _fallback_select_provider_model(
                config,
                "escalation_provider",
                "escalation_model",
                "Escalation",
                provider_choices=FALLBACK_PROVIDER_PRESETS,
            )
        elif answer == "s":
            val = input(f"  Тесты [{_tests_label(config)}]: ").strip()
            config = dataclasses.replace(config, test_command=val)
        elif answer == "1":
            val = input(f"  Файл [{config.plan_file}]: ").strip()
            if val:
                config = dataclasses.replace(config, plan_file=val)

        print()
