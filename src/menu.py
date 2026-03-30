"""Interactive TUI settings menu for tero go."""

from pathlib import Path
import yaml

from src.config import Config, FIXED_PROVIDER_MODELS, short_model_name

try:
    import questionary

    QUESTIONARY_AVAILABLE = True
except ImportError:
    QUESTIONARY_AVAILABLE = False

# Blackbox model IDs
CCG_MODEL_PRESETS = {
    "GLM-5    (blackboxai/z-ai/glm-5)": "blackboxai/z-ai/glm-5",
    "Turbo    (glm-5-turbo via Z.AI)": "glm-5-turbo",
    "ZAI      (glm-5.1 via Z.AI)": "glm-5.1",
    "Sonnet   (claude-sonnet-4-6)": "claude-sonnet-4-6",
    "Opus     (claude-opus-4-6)": "claude-opus-4-6",
    "Kimi     (kimi-k2.5)": "kimi-k2.5",
    "Ввести вручную...": "__custom__",
}

# Codex model IDs
CODEX_MODEL_PRESETS = {
    "GPT-5.4 (default)": "",
    "GPT-5.4 xhigh reasoning": "gpt-5.4",
    "o3": "o3",
    "o4-mini": "o4-mini",
    "Ввести вручную...": "__custom__",
}

# OpenCode model IDs (MIMO, Kimi, MiniMax, Nemotron — all free)
OPENCODE_MODEL_PRESETS = {
    "MIMO Pro  (free)": "opencode/mimo-v2-pro-free",
    "MIMO Omni (free)": "opencode/mimo-v2-omni-free",
    "MiniMax M2.5 (free)": "opencode/minimax-m2.5-free",
    "Kimi K2   (free)": "openrouter/moonshotai/kimi-k2:free",
    "Kimi K2.5": "openrouter/moonshotai/kimi-k2.5",
    "Nemotron 3 Super (free)": "opencode/nemotron-3-super-free",
    "Ввести вручную...": "__custom__",
}

KILO_MODEL_PRESETS = {
    "MIMO Pro  (free)": "kilo/xiaomi/mimo-v2-pro:free",
    "MiniMax M2.5 (free)": "kilo/minimax/minimax-m2.5:free",
}

# Provider choices
PROVIDER_PRESETS = {
    "BLACK (Blackbox/GLM-5)": "black",
    "TURBO (Z.AI / GLM-5 Turbo)": "turbo",
    "ZAI (Z.AI / GLM-5.1)": "zai",
    "Claude Pro (native)": "claude",
    "Codex (native CLI)": "codex",
    "OpenCode (MIMO/Kimi/free)": "opencode",
    "Kilo (MIMO/MiniMax)": "kilo",
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


def _provider_model_label(provider: str, model: str, default_text: str = "по умолчанию") -> str:
    """Render provider/model pair for compact menu display."""
    if not provider:
        return "выкл"
    return f"{provider} ({short_model_name(model) if model else default_text})"


def _fixed_model_for_provider(provider: str) -> str:
    """Return the model locked to a provider, or empty string if selectable."""
    if provider == "lite":
        provider = "zai"
    return FIXED_PROVIDER_MODELS.get(provider, "")


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
    return CCG_MODEL_PRESETS


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
    if model_id == "__custom__" and _custom_model_allowed(provider):
        model_id = questionary.text("Введи model ID:").ask() or getattr(
            config, model_field
        )
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
    config = Config(
        **{
            **config.__dict__,
            provider_field: provider,
            model_field: "" if not provider else getattr(config, model_field),
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
            "  Модели: mimo-pro, mimo-omni, minimax-m2.5, kimi-k2, kimi-k2.5, nemotron-3-super"
        )
        model = input(f"  {prompt_label} model [mimo-pro]: ").strip().lower() or "mimo-pro"
        model_map = {
            "mimo-pro": "opencode/mimo-v2-pro-free",
            "mimo-omni": "opencode/mimo-v2-omni-free",
            "minimax-m2.5": "opencode/minimax-m2.5-free",
            "kimi-k2": "openrouter/moonshotai/kimi-k2:free",
            "kimi-k2.5": "openrouter/moonshotai/kimi-k2.5",
            "nemotron-3-super": "opencode/nemotron-3-super-free",
        }
        return model_map.get(model, model)

    if provider == "kilo":
        print("  Модели: mimo-pro, minimax-m2.5")
        model = input(f"  {prompt_label} model [mimo-pro]: ").strip().lower() or "mimo-pro"
        model_map = {
            "mimo-pro": "kilo/xiaomi/mimo-v2-pro:free",
            "minimax-m2.5": "kilo/minimax/minimax-m2.5:free",
        }
        return model_map.get(model, model)

    return ""


def _fallback_select_provider_model(
    config: Config,
    provider_field: str,
    model_field: str,
    prompt_label: str,
    provider_choices: dict[str, str] | None = None,
) -> Config:
    """Shared provider/model picker for the plain-text fallback menu."""
    choice_map = provider_choices or PROVIDER_PRESETS
    allowed_values = list(dict.fromkeys(choice_map.values()))
    rendered_values = [value or "off" for value in allowed_values]
    current = getattr(config, provider_field) or ("off" if "" in allowed_values else "")

    print(f"  Провайдеры: {', '.join(rendered_values)}")
    raw_value = input(f"  {prompt_label} provider [{current}]: ").strip().lower()
    if not raw_value:
        return config

    if raw_value == "off" and "" in allowed_values:
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
    raw_provider = getattr(config, provider_field)
    raw_model = getattr(config, model_field)
    provider = raw_provider or config.coach_provider
    model = raw_model or (config.coach_model if not raw_provider else "")
    return _provider_model_label(provider, model)


def _format_batch_retry_counts(config: Config) -> str:
    """Format batch retry schedule as pre/judge/post."""
    return (
        f"{config.batch_pre_judge_attempts} / "
        f"{config.batch_judge_attempts} / "
        f"{config.batch_post_judge_attempts}"
    )


def _parse_batch_retry_counts(raw: str) -> tuple[int, int, int] | None:
    """Parse `pre judge post` or `pre/judge/post` retry schedule."""
    normalized = raw.replace("/", " ").replace(",", " ")
    parts = [part for part in normalized.split() if part]
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None

    counts = tuple(int(part) for part in parts)
    if sum(counts) <= 0:
        return None

    return counts


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
        verbose_display = "вкл" if config.verbose else "выкл"
        autonomous_display = "вкл" if config.autonomous else "выкл"
        batch_display = "вкл" if config.batch_mode else "выкл"
        batch_retry_display = _format_batch_retry_counts(config)
        tdd_display = "вкл" if config.tdd_mode else "выкл"
        review_display = "вкл" if config.code_review else "выкл"

        wd_display = config.working_dir.replace(str(Path.home()), "~")
        choices = [
            questionary.Choice(f"▶   Запустить", value="start"),
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
            questionary.Separator("─── batch роли ──────────────────────────"),
            questionary.Choice(
                f"    Pre-Coach:  {config.batch_pre_provider} ({short_model_name(config.batch_pre_model) if config.batch_pre_model else 'по умолчанию'}) [{config.batch_pre_judge_attempts}x]",
                value="batch_pre",
            ),
            questionary.Choice(
                f"    Judge:      {config.batch_judge_provider} ({short_model_name(config.batch_judge_model) if config.batch_judge_model else 'DEFAULT'}) [{config.batch_judge_attempts}x]",
                value="batch_judge",
            ),
            questionary.Choice(
                f"    Post-Coach: {config.batch_post_provider} ({short_model_name(config.batch_post_model) if config.batch_post_model else 'по умолчанию'}) [{config.batch_post_judge_attempts}x]",
                value="batch_post",
            ),
            questionary.Choice(
                f"    TestWriter: {config.test_writer_provider} ({short_model_name(config.test_writer_model) if config.test_writer_model else 'по умолчанию'})",
                value="test_writer",
            ),
            questionary.Separator("─── режимы ──────────────────────────────"),
            questionary.Choice(f"    TDD Mode:       {tdd_display}", value="tdd_mode"),
            questionary.Choice(
                f"    Code Review:    {review_display}", value="code_review"
            ),
            questionary.Separator("─── настройки ───────────────────────────"),
            questionary.Choice(
                f"    Рабочая папка:  {wd_display}", value="working_dir"
            ),
            questionary.Choice(
                f"    Файл плана:     {config.plan_file}", value="plan_file"
            ),
            questionary.Choice(
                f"    Макс. попыток:  {config.max_turns} (на шаг)", value="max_turns"
            ),
            questionary.Choice(
                f"    Verbose:        {verbose_display}", value="verbose"
            ),
            questionary.Choice(
                f"    Автономный:     {autonomous_display}", value="autonomous"
            ),
            questionary.Choice(
                f"    Batch Mode:     {batch_display}", value="batch_mode"
            ),
            questionary.Choice(
                f"    Batch Review:   {batch_retry_display}",
                value="batch_review_schedule",
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
            return config
        if answer == "save_default":
            _save_global_default(config)
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
        config = _sync_batch_roles_with_coach(
            config, previous_provider, previous_model
        )

    elif setting == "coach_fallback":
        config = _questionary_select_provider_model(
            config,
            "coach_fallback_provider",
            "coach_fallback_model",
            "fallback coach",
            provider_choices=FALLBACK_PROVIDER_PRESETS,
        )

    elif setting == "working_dir":
        wd_display = config.working_dir.replace(str(Path.home()), "~")
        val = questionary.text(
            f"Рабочая папка (текущая: {wd_display}):",
            default=wd_display,
        ).ask()
        if val:
            resolved = str(Path(val).expanduser().resolve())
            config = Config(**{**config.__dict__, "working_dir": resolved})

    elif setting == "coach_model":
        current = config.coach_model or ""
        label = f"Текущая модель коуча: {short_model_name(current) if current else 'по умолчанию'}"
        choices = list(CODEX_MODEL_PRESETS.keys())
        choice = questionary.select(label, choices=choices).ask()
        if choice:
            model_id = CODEX_MODEL_PRESETS[choice]
            if model_id == "__custom__":
                model_id = questionary.text("Введи model ID:").ask() or current
            config = Config(**{**config.__dict__, "coach_model": model_id})

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

    elif setting == "verbose":
        config = Config(**{**config.__dict__, "verbose": not config.verbose})

    elif setting == "autonomous":
        config = Config(**{**config.__dict__, "autonomous": not config.autonomous})

    elif setting == "batch_mode":
        config = Config(**{**config.__dict__, "batch_mode": not config.batch_mode})

    elif setting == "tdd_mode":
        config = Config(**{**config.__dict__, "tdd_mode": not config.tdd_mode})

    elif setting == "code_review":
        config = Config(**{**config.__dict__, "code_review": not config.code_review})

    elif setting == "batch_review_schedule":
        current = _format_batch_retry_counts(config)
        val = questionary.text(
            "Batch review retries в формате `до / судья / после` (например: 3 / 1 / 1):",
            default=current,
        ).ask()
        if val:
            counts = _parse_batch_retry_counts(val)
            if counts is not None:
                config = Config(
                    **{
                        **config.__dict__,
                        "batch_pre_judge_attempts": counts[0],
                        "batch_judge_attempts": counts[1],
                        "batch_post_judge_attempts": counts[2],
                    }
                )

    elif setting in ("batch_pre", "batch_judge", "batch_post", "test_writer"):
        prefix_map = {
            "batch_pre": ("batch_pre_provider", "batch_pre_model"),
            "batch_judge": ("batch_judge_provider", "batch_judge_model"),
            "batch_post": ("batch_post_provider", "batch_post_model"),
            "test_writer": ("test_writer_provider", "test_writer_model"),
        }
        prov_field, model_field = prefix_map[setting]
        current_prov = getattr(config, prov_field)
        choice = questionary.select(
            f"Провайдер для {setting} (текущий: {current_prov}):",
            choices=list(PROVIDER_PRESETS.keys()),
        ).ask()
        if choice:
            provider = PROVIDER_PRESETS[choice]
            config = Config(**{**config.__dict__, prov_field: provider})
            fixed_model = _fixed_model_for_provider(provider)
            if fixed_model:
                config = Config(**{**config.__dict__, model_field: fixed_model})
                return config
            model_choice = questionary.select(
                f"Модель для {setting}:",
                choices=list(_model_presets_for_provider(provider).keys()),
            ).ask()
            config = _resolve_model_choice(config, provider, model_field, model_choice)

    return config


def _save_global_default(config: Config) -> None:
    """Save current settings to ~/.g3/config.yaml as global defaults."""
    global_config_path = Path("~/.g3/config.yaml").expanduser()
    global_config_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "defaults": {
            "working_dir": config.working_dir,
            "plan_file": config.plan_file,
            "max_turns": config.max_turns,
            "verbose": config.verbose,
            "autonomous": config.autonomous,
            "batch_mode": config.batch_mode,
            "player_provider": config.player_provider,
            "coach_provider": config.coach_provider,
            "player_model": config.player_model,
            "coach_model": config.coach_model,
            "batch_pre_judge_attempts": config.batch_pre_judge_attempts,
            "batch_judge_attempts": config.batch_judge_attempts,
            "batch_post_judge_attempts": config.batch_post_judge_attempts,
            "tdd_mode": config.tdd_mode,
            "test_command": config.test_command,
            "code_review": config.code_review,
            "review_provider": config.review_provider,
            "review_model": config.review_model,
            "coach_fallback_provider": config.coach_fallback_provider,
            "coach_fallback_model": config.coach_fallback_model,
            "batch_pre_provider": config.batch_pre_provider,
            "batch_pre_model": config.batch_pre_model,
            "batch_judge_provider": config.batch_judge_provider,
            "batch_judge_model": config.batch_judge_model,
            "batch_post_provider": config.batch_post_provider,
            "batch_post_model": config.batch_post_model,
            "test_writer_provider": config.test_writer_provider,
            "test_writer_model": config.test_writer_model,
            "max_review_iterations": config.max_review_iterations,
        }
    }
    global_config_path.write_text(yaml.dump(data, allow_unicode=True))
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
        wd_display = config.working_dir.replace(str(Path.home()), "~")
        batch_retry_display = _format_batch_retry_counts(config)
        fallback_display = _provider_model_label(
            config.coach_fallback_provider,
            config.coach_fallback_model,
        )
        batch_pre_display = _fallback_effective_slot_label(
            config, "batch_pre_provider", "batch_pre_model"
        )
        batch_post_display = _fallback_effective_slot_label(
            config, "batch_post_provider", "batch_post_model"
        )
        judge_display = _provider_model_label(
            config.batch_judge_provider,
            config.batch_judge_model,
        )
        test_writer_display = _fallback_effective_slot_label(
            config, "test_writer_provider", "test_writer_model"
        )
        print(f"  [p] Player:        {config.player_provider} ({player_display})")
        print(f"  [c] Coach:         {config.coach_provider} ({coach_display})")
        print(f"  [f] Escalation:    {fallback_display}")
        print(f"  [t] TDD Mode:      {'вкл' if config.tdd_mode else 'выкл'}")
        print(f"  [r] Code Review:   {'вкл' if config.code_review else 'выкл'}")
        print(f"  [1] Рабочая папка: {wd_display}")
        print(f"  [2] Файл плана:    {config.plan_file}")
        print(f"  [3] Макс. попыток: {config.max_turns}")
        print(f"  [4] Verbose:       {'вкл' if config.verbose else 'выкл'}")
        print(f"  [5] Автономный:    {'вкл' if config.autonomous else 'выкл'}")
        print(f"  [6] Batch Mode:    {'вкл' if config.batch_mode else 'выкл'}")
        print(f"  [7] Batch Review:  {batch_retry_display}")
        print(f"  [8] Pre-Coach:     {batch_pre_display}")
        print(f"  [9] Judge:         {judge_display}")
        print(f"  [0] Post-Coach:    {batch_post_display}")
        print(f"  [w] TestWriter:    {test_writer_display}")
        print(f"  [s] Сохранить как default")
        print(f"  [Enter] Запустить")
        print(f"  [q] Выход\n")

        answer = input("  › ").strip().lower()

        if answer == "":
            return config
        if answer == "q":
            return None
        if answer == "s":
            _save_global_default(config)
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
        elif answer == "1":
            val = input(f"  Рабочая папка [{wd_display}]: ").strip()
            if val:
                resolved = str(Path(val).expanduser().resolve())
                config = Config(**{**config.__dict__, "working_dir": resolved})
        elif answer == "2":
            val = input(f"  Файл [{config.plan_file}]: ").strip()
            if val:
                config = Config(**{**config.__dict__, "plan_file": val})
        elif answer == "3":
            val = input(f"  Макс. попыток [{config.max_turns}]: ").strip()
            if val.isdigit():
                config = Config(**{**config.__dict__, "max_turns": int(val)})
        elif answer == "4":
            config = Config(**{**config.__dict__, "verbose": not config.verbose})
        elif answer == "5":
            config = Config(**{**config.__dict__, "autonomous": not config.autonomous})
        elif answer == "t":
            config = Config(**{**config.__dict__, "tdd_mode": not config.tdd_mode})
        elif answer == "r":
            config = Config(
                **{**config.__dict__, "code_review": not config.code_review}
            )
        elif answer == "6":
            config = Config(**{**config.__dict__, "batch_mode": not config.batch_mode})
        elif answer == "7":
            val = input(
                f"  Batch review retries [{batch_retry_display}] (до / судья / после): "
            ).strip()
            if val:
                counts = _parse_batch_retry_counts(val)
                if counts is not None:
                    config = Config(
                        **{
                            **config.__dict__,
                            "batch_pre_judge_attempts": counts[0],
                            "batch_judge_attempts": counts[1],
                            "batch_post_judge_attempts": counts[2],
                        }
                    )
        elif answer == "8":
            config = _fallback_select_provider_model(
                config, "batch_pre_provider", "batch_pre_model", "Pre-Coach"
            )
        elif answer == "9":
            config = _fallback_select_provider_model(
                config, "batch_judge_provider", "batch_judge_model", "Judge"
            )
        elif answer == "0":
            config = _fallback_select_provider_model(
                config, "batch_post_provider", "batch_post_model", "Post-Coach"
            )
        elif answer == "w":
            config = _fallback_select_provider_model(
                config, "test_writer_provider", "test_writer_model", "TestWriter"
            )

        print()
