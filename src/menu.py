"""Interactive TUI settings menu for tero go."""

from pathlib import Path
import yaml

from src.config import Config, short_model_name, _DEFAULT_CONTEXT_LIMIT

try:
    import questionary

    QUESTIONARY_AVAILABLE = True
except ImportError:
    QUESTIONARY_AVAILABLE = False

# Codex model IDs.
# Reasoning effort is forced to "medium" by the codex provider factory
# (see providers/registry.py), so users can't accidentally inherit a global
# `xhigh` from ~/.codex/config.toml when picking a coach/judge model here.
CODEX_MODEL_PRESETS = {
    "GPT-5.4 (medium)": "gpt-5.4",
    "Default (~/.codex/config.toml)": "",
}

# OpenCode model IDs
OPENCODE_MODEL_PRESETS = {
    "MiniMax M2.5 (free)": "opencode/minimax-m2.5-free",
    "Z.AI GLM-5.1 (direct)": "zai/glm-5.1",
    "Z.AI GLM-5 Turbo (openrouter)": "openrouter/z-ai/glm-5-turbo",
}

KILO_MODEL_PRESETS = {
    "MIMO Pro  (free)": "kilo/xiaomi/mimo-v2-pro:free",
    "MiniMax M2.5 (free)": "kilo/minimax/minimax-m2.5:free",
}

GEMINI_MODEL_PRESETS = {
    "Gemini 2.5 Flash": "gemini-2.5-flash",
    "Gemini 2.5 Pro": "gemini-2.5-pro",
    "Gemini 2.0 Flash": "gemini-2.0-flash",
}


def _format_context_limit(limit: int) -> str:
    """Format context limit for menu display."""
    if limit == _DEFAULT_CONTEXT_LIMIT:
        return "авто (по модели)"
    if limit < 1000:
        return f"{limit}"
    if limit >= 1_000_000 and limit % 1_000_000 == 0:
        return f"{limit // 1_000_000}M"
    return f"{limit // 1000}K"


# Provider choices
PROVIDER_PRESETS = {
    "ZAI (Z.AI / GLM-5.1)": "zai",
    "Claude Pro (native)": "claude",
    "Codex (native CLI)": "codex",
    "OpenCode (MIMO/Kimi/Z.AI)": "opencode",
    "Kilo (MIMO/MiniMax)": "kilo",
    "Gemini (Google CLI)": "gemini",
}

# Claude model choices (for native provider)
CLAUDE_MODEL_PRESETS = {
    "Sonnet (balanced)": "sonnet",
    "Opus   (most capable)": "opus",
    "Haiku  (fast)": "haiku",
}

FALLBACK_PROVIDER_PRESETS = {
    "Отключить escalation": "",
    **PROVIDER_PRESETS,
}


def _provider_model_label(
    provider: str, model: str, default_text: str = "по умолчанию"
) -> str:
    """Render provider/model pair for compact menu display."""
    if not provider:
        return "выкл"
    return f"{provider} ({short_model_name(model) if model else default_text})"


def _fixed_model_for_provider(provider: str) -> str:
    """Return the model locked to a provider, or empty string if selectable.

    IMPORTANT: ZAI must return a fixed model here.  Without it the menu calls
    questionary.select() with an empty choices list and crashes.
    See test_menu_bugs.py::TestZaiFixedModel for the regression test.
    """
    if provider in ("lite", "zai"):
        return "glm-5.1"
    return ""


def _effective_provider_model(
    provider: str,
    model: str,
    *,
    fallback_provider: str = "",
    fallback_model: str = "",
) -> tuple[str, str]:
    """Resolve the provider/model pair the runtime will actually use."""
    effective_provider = provider or fallback_provider
    effective_model = model or (fallback_model if not provider else "")
    if effective_provider and not effective_model:
        effective_model = _fixed_model_for_provider(effective_provider)
    return effective_provider, effective_model


def _model_presets_for_provider(provider: str) -> dict[str, str]:
    """Return menu presets for a selectable provider."""
    if provider == "claude":
        return CLAUDE_MODEL_PRESETS
    if provider == "codex":
        return CODEX_MODEL_PRESETS
    if provider == "opencode":
        return OPENCODE_MODEL_PRESETS
    if provider == "kilo":
        return KILO_MODEL_PRESETS
    if provider == "gemini":
        return GEMINI_MODEL_PRESETS
    return {}


def _custom_model_allowed(provider: str) -> bool:
    """Return True when the provider picker supports manual model entry."""
    return provider in {"codex", "opencode"}


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
    return Config(**{**config.__dict__, model_field: model_id})


def _sync_batch_roles_with_coach(
    config: Config,
    previous_provider: str,
    previous_model: str,
) -> Config:
    """Keep batch pre/post reviewers aligned with coach after an explicit coach change."""
    updates = dict(config.__dict__)

    if (
        config.batch_pre_provider == previous_provider
        and config.batch_pre_model == previous_model
    ):
        updates["batch_pre_provider"] = config.coach_provider
        updates["batch_pre_model"] = config.coach_model

    if (
        config.batch_post_provider == previous_provider
        and config.batch_post_model == previous_model
    ):
        updates["batch_post_provider"] = config.coach_provider
        updates["batch_post_model"] = config.coach_model

    return Config(**updates)


def _questionary_select_provider_model(
    config: Config,
    provider_field: str,
    model_field: str,
    prompt_label: str,
    provider_choices: dict[str, str] | None = None,
) -> Config:
    """Shared questionary provider/model picker."""
    choices = list((provider_choices or PROVIDER_PRESETS).keys())
    current = getattr(config, provider_field)
    choice = questionary.select(
        f"Провайдер для {prompt_label} (текущий: {current or 'выкл'}):",
        choices=choices,
    ).ask()
    if not choice:
        return config

    provider = (provider_choices or PROVIDER_PRESETS)[choice]
    next_model = getattr(config, model_field) if provider == current else ""
    config = Config(
        **{
            **config.__dict__,
            provider_field: provider,
            model_field: "" if not provider else next_model,
        }
    )

    if not provider:
        return Config(**{**config.__dict__, model_field: ""})

    fixed_model = _fixed_model_for_provider(provider)
    if fixed_model:
        return Config(**{**config.__dict__, model_field: fixed_model})

    model_choice = questionary.select(
        f"Модель для {prompt_label}:",
        choices=list(_model_presets_for_provider(provider).keys()),
    ).ask()
    return _resolve_model_choice(config, provider, model_field, model_choice)


def _fallback_prompt_model(provider: str, prompt_label: str) -> str:
    """Prompt for a provider-specific model in the plain-text fallback menu."""
    fixed_model = _fixed_model_for_provider(provider)
    if fixed_model:
        return fixed_model

    if provider == "claude":
        print("  Модели: sonnet, opus, haiku")
        return input(f"  {prompt_label} model [sonnet]: ").strip() or "sonnet"

    if provider == "codex":
        print("  Модели: default, gpt-5.4, o3, o4-mini")
        model = input(f"  {prompt_label} model [default]: ").strip()
        return "" if model.lower() == "default" else model

    if provider == "opencode":
        print(
            "  Модели: mimo-pro, mimo-omni, minimax-m2.5, kimi-k2, kimi-k2.5, glm-5.1, nemotron-3-super"
        )
        model = (
            input(f"  {prompt_label} model [mimo-pro]: ").strip().lower() or "mimo-pro"
        )
        model_map = {
            "mimo-pro": "opencode/mimo-v2-pro-free",
            "mimo-omni": "opencode/mimo-v2-omni-free",
            "minimax-m2.5": "opencode/minimax-m2.5-free",
            "kimi-k2": "openrouter/moonshotai/kimi-k2:free",
            "kimi-k2.5": "openrouter/moonshotai/kimi-k2.5",
            "glm-5.1": "zai/glm-5.1",
            "zai": "zai/glm-5.1",
            "nemotron-3-super": "opencode/nemotron-3-super-free",
        }
        return model_map.get(model, model)

    if provider == "kilo":
        print("  Модели: mimo-pro, minimax-m2.5")
        model = (
            input(f"  {prompt_label} model [mimo-pro]: ").strip().lower() or "mimo-pro"
        )
        model_map = {
            "mimo-pro": "kilo/xiaomi/mimo-v2-pro:free",
            "minimax-m2.5": "kilo/minimax/minimax-m2.5:free",
        }
        return model_map.get(model, model)

    if provider == "gemini":
        print("  Модели: gemini-2.5-flash, gemini-2.5-pro, gemini-2.0-flash")
        model = (
            input(f"  {prompt_label} model [gemini-2.5-flash]: ").strip().lower()
            or "gemini-2.5-flash"
        )
        return model

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

    config = Config(**{**config.__dict__, provider_field: provider})
    if not provider:
        return Config(**{**config.__dict__, model_field: ""})

    model = _fallback_prompt_model(provider, prompt_label)
    return Config(**{**config.__dict__, model_field: model})


def _fallback_effective_slot_label(
    config: Config,
    provider_field: str,
    model_field: str,
) -> str:
    """Render the effective provider/model label for a batch-adjacent slot."""
    provider, model = _effective_provider_model(
        getattr(config, provider_field),
        getattr(config, model_field),
        fallback_provider=config.coach_provider,
        fallback_model=config.coach_model,
    )
    return _provider_model_label(provider, model)


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
        coach_display = (
            short_model_name(config.coach_model)
            if config.coach_model
            else "по умолчанию"
        )
        player_display = (
            short_model_name(config.player_model)
            if config.player_model
            else "по умолчанию"
        )
        fallback_display = _provider_model_label(
            config.coach_fallback_provider,
            config.coach_fallback_model,
        )
        judge_provider, judge_model = _effective_provider_model(
            config.batch_judge_provider,
            config.batch_judge_model,
        )
        judge_display = _provider_model_label(judge_provider, judge_model)
        choices = [
            questionary.Choice("▶   Запустить", value="start"),
            questionary.Separator("─── провайдеры ──────────────────────────"),
            questionary.Choice(
                f"    Player:         {config.player_provider} ({player_display})",
                value="player_provider",
            ),
            questionary.Choice(
                f"    Coach:          {config.coach_provider} ({coach_display})",
                value="coach_provider",
            ),
            questionary.Choice(
                f"    Escalation:     {fallback_display}",
                value="coach_fallback",
            ),
            questionary.Choice(
                f"    Judge:          {judge_display}",
                value="batch_judge",
            ),
            questionary.Separator("─── настройки ───────────────────────────"),
            questionary.Choice(
                f"    Файл плана:     {config.plan_file}", value="plan_file"
            ),
            questionary.Choice(
                f"    Макс. попыток:  {config.max_turns} (на шаг)", value="max_turns"
            ),
            questionary.Choice(
                f"    Context Limit:  {_format_context_limit(config.context_limit)}",
                value="context_limit",
            ),
            questionary.Separator("─────────────────────────────────────────"),
            questionary.Choice("💾  Сохранить как default", value="save_default"),
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
        if answer == "start_debug":
            config = run_debugger_menu(config)
            continue

        config = _edit_setting_questionary(config, answer)


def _edit_setting_questionary(config: Config, setting: str) -> Config:
    """Edit a single setting using questionary prompts."""
    if setting == "player_provider":
        config = _questionary_select_provider_model(
            config, "player_provider", "player_model", "player"
        )

    elif setting == "coach_provider":
        previous_provider = config.coach_provider
        previous_model = config.coach_model
        config = _questionary_select_provider_model(
            config, "coach_provider", "coach_model", "coach"
        )
        config = _sync_batch_roles_with_coach(config, previous_provider, previous_model)

    elif setting == "coach_fallback":
        config = _questionary_select_provider_model(
            config,
            "coach_fallback_provider",
            "coach_fallback_model",
            "fallback coach",
            provider_choices=FALLBACK_PROVIDER_PRESETS,
        )

    elif setting == "coach_model":
        _, current_model = _effective_provider_model(
            config.coach_provider,
            config.coach_model,
        )
        label = (
            "Текущая модель коуча: "
            f"{short_model_name(current_model) if current_model else 'по умолчанию'}"
        )
        choices = list(_model_presets_for_provider(config.coach_provider).keys())
        choice = questionary.select(label, choices=choices).ask()
        if choice:
            config = _resolve_model_choice(
                config, config.coach_provider, "coach_model", choice
            )

    elif setting == "max_turns":
        val = questionary.text(
            f"Макс. попыток на шаг (текущее: {config.max_turns}):"
        ).ask()
        if val and val.isdigit():
            config = Config(**{**config.__dict__, "max_turns": int(val)})

    elif setting == "plan_file":
        val = questionary.text(f"Файл плана (текущий: {config.plan_file}):").ask()
        if val:
            config = Config(**{**config.__dict__, "plan_file": val})

    elif setting == "batch_judge":
        config = _questionary_select_provider_model(
            config,
            "batch_judge_provider",
            "batch_judge_model",
            "Judge",
        )

    elif setting == "context_limit":
        choices = [
            questionary.Choice("Авто (по модели) — рекомендуется", value="auto"),
            questionary.Choice("200K", value="200000"),
            questionary.Choice("500K", value="500000"),
            questionary.Choice("1M (максимум Claude/Codex)", value="1000000"),
            questionary.Choice("Ввести вручную", value="custom"),
        ]
        val = questionary.select(
            f"Context limit (текущий: {_format_context_limit(config.context_limit)}):",
            choices=choices,
        ).ask()
        if val == "auto":
            config = Config(
                **{**config.__dict__, "context_limit": _DEFAULT_CONTEXT_LIMIT}
            )
        elif val == "custom":
            raw = questionary.text("Лимит в токенах:").ask()
            if raw and raw.isdigit():
                config = Config(**{**config.__dict__, "context_limit": int(raw)})
        elif val and val.isdigit():
            config = Config(**{**config.__dict__, "context_limit": int(val)})

    return config


def _save_global_default(config: Config) -> None:
    """Save current settings to ~/.g3/config.yaml as global defaults."""
    global_config_path = Path.home() / ".g3" / "config.yaml"
    global_config_path.parent.mkdir(parents=True, exist_ok=True)

    data = {"defaults": dict(config.__dict__)}
    global_config_path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False))
    wd = config.working_dir.replace(str(Path.home()), "~")
    print(f"\n  Сохранено в ~/.g3/config.yaml (рабочая папка: {wd})\n")


def _fallback_menu(config: Config) -> Config | None:
    """Plain text fallback when questionary is not installed."""
    print("\n⚙  tero — настройка")
    print("  (установи questionary для красивого меню: pip install questionary)\n")

    while True:
        coach_display = (
            short_model_name(config.coach_model)
            if config.coach_model
            else "по умолчанию"
        )
        player_display = (
            short_model_name(config.player_model)
            if config.player_model
            else "по умолчанию"
        )
        fallback_display = _provider_model_label(
            config.coach_fallback_provider,
            config.coach_fallback_model,
        )
        judge_display = _provider_model_label(
            config.batch_judge_provider,
            config.batch_judge_model,
        )
        print(f"  [p] Player:        {config.player_provider} ({player_display})")
        print(f"  [c] Coach:         {config.coach_provider} ({coach_display})")
        print(f"  [f] Escalation:    {fallback_display}")
        print(f"  [j] Judge:         {judge_display}")
        print(f"  [1] Файл плана:    {config.plan_file}")
        print(f"  [2] Макс. попыток: {config.max_turns}")
        print(f"  [3] Context Limit: {_format_context_limit(config.context_limit)}")
        print(f"  [Enter] Запустить (сохраняет настройки)")
        print(f"  [q] Выход\n")

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
        elif answer == "c":
            previous_provider = config.coach_provider
            previous_model = config.coach_model
            config = _fallback_select_provider_model(
                config, "coach_provider", "coach_model", "Coach"
            )
            config = _sync_batch_roles_with_coach(
                config, previous_provider, previous_model
            )
        elif answer == "f":
            config = _fallback_select_provider_model(
                config,
                "coach_fallback_provider",
                "coach_fallback_model",
                "Fallback",
                provider_choices=FALLBACK_PROVIDER_PRESETS,
            )
        elif answer == "j":
            config = _fallback_select_provider_model(
                config, "batch_judge_provider", "batch_judge_model", "Judge"
            )
        elif answer == "1":
            val = input(f"  Файл [{config.plan_file}]: ").strip()
            if val:
                config = Config(**{**config.__dict__, "plan_file": val})
        elif answer == "2":
            val = input(f"  Макс. попыток [{config.max_turns}]: ").strip()
            if val.isdigit():
                config = Config(**{**config.__dict__, "max_turns": int(val)})
        elif answer == "3":
            print("  [a] Авто  [1] 200K  [2] 500K  [3] 1M  [4] Вручную")
            choice = input("  › ").strip().lower()
            if choice == "a":
                config = Config(
                    **{**config.__dict__, "context_limit": _DEFAULT_CONTEXT_LIMIT}
                )
            elif choice == "1":
                config = Config(**{**config.__dict__, "context_limit": 200_000})
            elif choice == "2":
                config = Config(**{**config.__dict__, "context_limit": 500_000})
            elif choice == "3":
                config = Config(**{**config.__dict__, "context_limit": 1_000_000})
            elif choice == "4":
                raw = input("  Лимит в токенах: ").strip()
                if raw.isdigit():
                    config = Config(**{**config.__dict__, "context_limit": int(raw)})

        print()


# ── Debugger menu ─────────────────────────────────────────────────────────────

DEBUG_INTENSITY_PRESETS = {
    "Low  (1 pass — structural analysis)": "low",
    "Medium (2 passes — structural + anchor)": "medium",
    "High  (5 passes — all audits)": "high",
}

DEBUG_LIMIT_PRESETS = {
    "Infinite": ("infinite", 0),
    "5 iterations": ("iterations", 5),
    "10 iterations": ("iterations", 10),
    "20 iterations": ("iterations", 20),
    "10 minutes": ("time", 10),
    "30 minutes": ("time", 30),
    "60 minutes": ("time", 60),
}


def run_debugger_menu(config: "Config") -> "Config":
    """Interactive menu for the debugger command.

    Returns updated config with debug_* fields set.
    """
    if not QUESTIONARY_AVAILABLE:
        return _fallback_debugger_menu(config)

    while True:
        player_display = _provider_model_label(
            config.debug_player_provider, config.debug_player_model
        )
        tester_display = _provider_model_label(
            config.debug_tester_provider, config.debug_tester_model
        )
        fixer_display = _provider_model_label(
            config.debug_fixer_provider, config.debug_fixer_model
        )
        synthesizer_display = _provider_model_label(
            config.debug_synthesizer_provider, config.debug_synthesizer_model
        )

        intensity_label = next(
            (
                k
                for k, v in DEBUG_INTENSITY_PRESETS.items()
                if v == config.debug_intensity
            ),
            config.debug_intensity,
        )
        limit_label = next(
            (
                k
                for k, (m, v) in DEBUG_LIMIT_PRESETS.items()
                if m == config.debug_limit_mode and v == config.debug_limit_value
            ),
            f"{config.debug_limit_mode}/{config.debug_limit_value}",
        )

        choices = [
            questionary.Choice("▶   Запустить Debugger", value="start"),
            questionary.Separator("─── агенты ──────────────────────────────"),
            questionary.Choice(
                f"    Player (ищет баги):       {player_display}", value="player"
            ),
            questionary.Choice(
                f"    Tester (пишет тесты):     {tester_display}", value="tester"
            ),
            questionary.Choice(
                f"    Fixer (чинит баги):       {fixer_display}", value="fixer"
            ),
            questionary.Choice(
                f"    Synthesizer (входы):      {synthesizer_display}",
                value="synthesizer",
            ),
            questionary.Separator("─── параметры ───────────────────────────"),
            questionary.Choice(
                f"    Интенсивность:        {intensity_label}", value="intensity"
            ),
            questionary.Choice(
                f"    Лимит:                {limit_label}", value="limit"
            ),
            questionary.Separator("─────────────────────────────────────────"),
            questionary.Choice("←   Назад", value="back"),
        ]

        answer = questionary.select(
            "🔍 Debugger — настройка  (↑↓ выбор, Enter)",
            choices=choices,
            use_shortcuts=False,
        ).ask()

        if answer is None or answer == "back":
            return config
        if answer == "start":
            return config

        if answer == "player":
            config = _questionary_select_provider_model(
                config, "debug_player_provider", "debug_player_model", "Player"
            )
        elif answer == "tester":
            config = _questionary_select_provider_model(
                config, "debug_tester_provider", "debug_tester_model", "Tester"
            )
        elif answer == "fixer":
            config = _questionary_select_provider_model(
                config, "debug_fixer_provider", "debug_fixer_model", "Fixer"
            )
        elif answer == "synthesizer":
            config = _questionary_select_provider_model(
                config,
                "debug_synthesizer_provider",
                "debug_synthesizer_model",
                "Synthesizer",
            )
        elif answer == "intensity":
            choice = questionary.select(
                "Интенсивность:",
                choices=list(DEBUG_INTENSITY_PRESETS.keys()),
            ).ask()
            if choice:
                config = Config(
                    **{
                        **config.__dict__,
                        "debug_intensity": DEBUG_INTENSITY_PRESETS[choice],
                    }
                )
        elif answer == "limit":
            choice = questionary.select(
                "Лимит:",
                choices=list(DEBUG_LIMIT_PRESETS.keys()),
            ).ask()
            if choice:
                mode, value = DEBUG_LIMIT_PRESETS[choice]
                config = Config(
                    **{
                        **config.__dict__,
                        "debug_limit_mode": mode,
                        "debug_limit_value": value,
                    }
                )


def _fallback_debugger_menu(config: "Config") -> "Config":
    """Plain-text fallback for debugger menu when questionary is not available."""
    print("\n🔍 Debugger — настройка")
    print(f"  Player:       {config.debug_player_provider}")
    print(f"  Tester:       {config.debug_tester_provider}")
    print(f"  Fixer:        {config.debug_fixer_provider}")
    print(f"  Synthesizer:  {config.debug_synthesizer_provider}")
    print(f"  Intensity: {config.debug_intensity}")
    print(f"  Limit:     {config.debug_limit_mode}/{config.debug_limit_value}")
    print("\n  (установи questionary для интерактивного меню: pip install questionary)")
    print("  Нажми Enter для продолжения, q для выхода")
    answer = input("  › ").strip().lower()
    if answer == "q":
        import sys

        sys.exit(0)
    return config


# ── LDB menu ──────────────────────────────────────────────────────────────────

LDB_MODE_PRESETS = {
    "Mode 2 (input+find+test — read-only)": 2,
    "Mode 3 (+fix +auto-commit)": 3,
}


def run_ldb_menu(config: "Config") -> "Config":
    """Interactive menu for the ldb command.

    Returns updated config with ldb_* fields set.
    """
    if not QUESTIONARY_AVAILABLE:
        return _fallback_ldb_menu(config)

    import questionary

    while True:
        input_display = _provider_model_label(
            config.ldb_input_provider, config.ldb_input_model
        )
        player_display = _provider_model_label(
            config.ldb_player_provider, config.ldb_player_model
        )
        tester_display = _provider_model_label(
            config.ldb_tester_provider, config.ldb_tester_model
        )
        fixer_display = _provider_model_label(
            config.ldb_fixer_provider, config.ldb_fixer_model
        )

        mode_label = next(
            (k for k, v in LDB_MODE_PRESETS.items() if v == config.ldb_mode),
            f"Mode {config.ldb_mode}",
        )

        choices = [
            questionary.Choice("▶   Запустить LDB", value="start"),
            questionary.Separator("─── агенты ──────────────────────────────"),
            questionary.Choice(
                f"    Input  (синтез входов):  {input_display}", value="input"
            ),
            questionary.Choice(
                f"    Player (ищет баги):      {player_display}", value="player"
            ),
            questionary.Choice(
                f"    Tester (пишет тесты):    {tester_display}", value="tester"
            ),
            questionary.Choice(
                f"    Fixer  (чинит баги):     {fixer_display}", value="fixer"
            ),
            questionary.Separator("─── параметры ───────────────────────────"),
            questionary.Choice(f"    Режим:               {mode_label}", value="mode"),
            questionary.Choice(
                f"    Max итераций:        {config.ldb_max_iterations}",
                value="max_iterations",
            ),
            questionary.Choice(
                f"    Target файл:         {config.ldb_target_file or '(не задан)'}",
                value="target_file",
            ),
            questionary.Choice(
                f"    Target функция:      {config.ldb_target_entry or '(не задана)'}",
                value="target_entry",
            ),
            questionary.Choice(
                f"    Scope all:           {'да' if config.ldb_scope_all else 'нет'}",
                value="scope_all",
            ),
            questionary.Separator("─────────────────────────────────────────"),
            questionary.Choice("←   Назад", value="back"),
        ]

        answer = questionary.select(
            "🔬 LDB — настройка  (↑↓ выбор, Enter)",
            choices=choices,
            use_shortcuts=False,
        ).ask()

        if answer is None or answer == "back":
            return config
        if answer == "start":
            return config

        if answer == "input":
            config = _questionary_select_provider_model(
                config, "ldb_input_provider", "ldb_input_model", "Input"
            )
        elif answer == "player":
            config = _questionary_select_provider_model(
                config, "ldb_player_provider", "ldb_player_model", "Player"
            )
        elif answer == "tester":
            config = _questionary_select_provider_model(
                config, "ldb_tester_provider", "ldb_tester_model", "Tester"
            )
        elif answer == "fixer":
            config = _questionary_select_provider_model(
                config, "ldb_fixer_provider", "ldb_fixer_model", "Fixer"
            )
        elif answer == "mode":
            choice = questionary.select(
                "Режим LDB:",
                choices=list(LDB_MODE_PRESETS.keys()),
            ).ask()
            if choice:
                config = Config(
                    **{
                        **config.__dict__,
                        "ldb_mode": LDB_MODE_PRESETS[choice],
                    }
                )
        elif answer == "max_iterations":
            val = questionary.text(
                f"Max итераций (текущее: {config.ldb_max_iterations}):"
            ).ask()
            if val and val.isdigit():
                config = Config(**{**config.__dict__, "ldb_max_iterations": int(val)})
        elif answer == "target_file":
            val = questionary.text(
                f"Target файл (текущий: {config.ldb_target_file}):"
            ).ask()
            if val is not None:
                config = Config(**{**config.__dict__, "ldb_target_file": val.strip()})
        elif answer == "target_entry":
            val = questionary.text(
                f"Target функция (текущая: {config.ldb_target_entry}):"
            ).ask()
            if val is not None:
                config = Config(**{**config.__dict__, "ldb_target_entry": val.strip()})
        elif answer == "scope_all":
            config = Config(
                **{**config.__dict__, "ldb_scope_all": not config.ldb_scope_all}
            )


def _fallback_ldb_menu(config: "Config") -> "Config":
    """Plain-text fallback for LDB menu when questionary is not available."""
    while True:
        input_display = _provider_model_label(
            config.ldb_input_provider, config.ldb_input_model
        )
        player_display = _provider_model_label(
            config.ldb_player_provider, config.ldb_player_model
        )
        tester_display = _provider_model_label(
            config.ldb_tester_provider, config.ldb_tester_model
        )
        fixer_display = _provider_model_label(
            config.ldb_fixer_provider, config.ldb_fixer_model
        )

        print("\n🔬 LDB — настройка")
        print(f"  [i] Input:        {input_display}")
        print(f"  [p] Player:       {player_display}")
        print(f"  [t] Tester:       {tester_display}")
        print(f"  [f] Fixer:        {fixer_display}")
        print(f"  [m] Режим:        {config.ldb_mode}")
        print(f"  [1] Max итераций: {config.ldb_max_iterations}")
        print(f"  [2] Target файл:  {config.ldb_target_file or '(не задан)'}")
        print(f"  [3] Target ф-я:   {config.ldb_target_entry or '(не задана)'}")
        print(f"  [4] Scope all:    {'да' if config.ldb_scope_all else 'нет'}")
        print(f"  [Enter] Запустить")
        print(f"  [q] Назад\n")

        answer = input("  › ").strip().lower()

        if answer == "":
            return config
        if answer == "q":
            return config
        elif answer == "i":
            config = _fallback_select_provider_model(
                config, "ldb_input_provider", "ldb_input_model", "Input"
            )
        elif answer == "p":
            config = _fallback_select_provider_model(
                config, "ldb_player_provider", "ldb_player_model", "Player"
            )
        elif answer == "t":
            config = _fallback_select_provider_model(
                config, "ldb_tester_provider", "ldb_tester_model", "Tester"
            )
        elif answer == "f":
            config = _fallback_select_provider_model(
                config, "ldb_fixer_provider", "ldb_fixer_model", "Fixer"
            )
        elif answer == "m":
            raw = input(f"  Режим (2 или 3) [{config.ldb_mode}]: ").strip()
            if raw in ("2", "3"):
                config = Config(**{**config.__dict__, "ldb_mode": int(raw)})
        elif answer == "1":
            val = input(f"  Max итераций [{config.ldb_max_iterations}]: ").strip()
            if val.isdigit():
                config = Config(**{**config.__dict__, "ldb_max_iterations": int(val)})
        elif answer == "2":
            val = input(f"  Target файл [{config.ldb_target_file}]: ").strip()
            if val is not None:
                config = Config(**{**config.__dict__, "ldb_target_file": val})
        elif answer == "3":
            val = input(f"  Target функция [{config.ldb_target_entry}]: ").strip()
            if val is not None:
                config = Config(**{**config.__dict__, "ldb_target_entry": val})
        elif answer == "4":
            config = Config(
                **{**config.__dict__, "ldb_scope_all": not config.ldb_scope_all}
            )

        print()
