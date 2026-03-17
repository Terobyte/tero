"""Interactive TUI settings menu for tero go."""

from pathlib import Path
import yaml

from src.config import Config, short_model_name

try:
    import questionary
    QUESTIONARY_AVAILABLE = True
except ImportError:
    QUESTIONARY_AVAILABLE = False

# Known model IDs
MODEL_PRESETS = {
    "GLM-5    (blackboxai/z-ai/glm-5)": "blackboxai/z-ai/glm-5",
    "Sonnet   (claude-sonnet-4-6)": "claude-sonnet-4-6",
    "Opus     (claude-opus-4-6)": "claude-opus-4-6",
    "Kimi     (kimi-k2.5)": "kimi-k2.5",
    "Ввести вручную...": "__custom__",
}

# Provider choices
PROVIDER_PRESETS = {
    "CCG (Blackbox/GLM-5)": "ccg",
    "Claude Pro (native)": "claude",
}

# Claude model choices (for native provider)
CLAUDE_MODEL_PRESETS = {
    "Sonnet (balanced)": "sonnet",
    "Opus   (most capable)": "opus",
    "Haiku  (fast)": "haiku",
}


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
        coach_display = short_model_name(config.coach_model) if config.coach_model else "по умолчанию"
        player_display = short_model_name(config.player_model) if config.player_model else "по умолчанию"
        verbose_display = "вкл" if config.verbose else "выкл"
        autonomous_display = "вкл" if config.autonomous else "выкл"

        wd_display = config.working_dir.replace(str(Path.home()), "~")
        choices = [
            questionary.Choice(f"▶   Запустить", value="start"),
            questionary.Separator("─── провайдеры ──────────────────────────"),
            questionary.Choice(f"    Player:         {config.player_provider} ({player_display})", value="player_provider"),
            questionary.Choice(f"    Coach:          {config.coach_provider} ({coach_display})", value="coach_provider"),
            questionary.Separator("─── настройки ───────────────────────────"),
            questionary.Choice(f"    Рабочая папка:  {wd_display}", value="working_dir"),
            questionary.Choice(f"    Файл плана:     {config.plan_file}", value="plan_file"),
            questionary.Choice(f"    Макс. попыток:  {config.max_turns} (на шаг)", value="max_turns"),
            questionary.Choice(f"    Verbose:        {verbose_display}", value="verbose"),
            questionary.Choice(f"    Автономный:     {autonomous_display}", value="autonomous"),
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
    import questionary

    if setting == "player_provider":
        current = config.player_provider
        choices = list(PROVIDER_PRESETS.keys())
        choice = questionary.select(
            f"Провайдер для player (текущий: {current}):",
            choices=choices,
        ).ask()
        if choice:
            provider = PROVIDER_PRESETS[choice]
            config = Config(**{**config.__dict__, "player_provider": provider})
            # Ask for model based on provider
            if provider == "claude":
                model_choice = questionary.select(
                    "Модель для player:",
                    choices=list(CLAUDE_MODEL_PRESETS.keys()),
                ).ask()
                if model_choice:
                    config = Config(**{**config.__dict__, "player_model": CLAUDE_MODEL_PRESETS[model_choice]})
            else:
                config = Config(**{**config.__dict__, "player_model": ""})

    elif setting == "coach_provider":
        current = config.coach_provider
        choices = list(PROVIDER_PRESETS.keys())
        choice = questionary.select(
            f"Провайдер для coach (текущий: {current}):",
            choices=choices,
        ).ask()
        if choice:
            provider = PROVIDER_PRESETS[choice]
            config = Config(**{**config.__dict__, "coach_provider": provider})
            # Ask for model based on provider
            if provider == "claude":
                model_choice = questionary.select(
                    "Модель для coach:",
                    choices=list(CLAUDE_MODEL_PRESETS.keys()),
                ).ask()
                if model_choice:
                    config = Config(**{**config.__dict__, "coach_model": CLAUDE_MODEL_PRESETS[model_choice]})
            else:
                # For CCG, use model presets
                model_choices = list(MODEL_PRESETS.keys())
                model_choice = questionary.select(
                    "Модель для coach:",
                    choices=model_choices,
                ).ask()
                if model_choice:
                    model_id = MODEL_PRESETS[model_choice]
                    if model_id == "__custom__":
                        model_id = questionary.text("Введи model ID:").ask() or config.coach_model
                    config = Config(**{**config.__dict__, "coach_model": model_id})

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
        choices = list(MODEL_PRESETS.keys())
        choice = questionary.select(label, choices=choices).ask()
        if choice:
            model_id = MODEL_PRESETS[choice]
            if model_id == "__custom__":
                model_id = questionary.text("Введи model ID:").ask() or current
            config = Config(
                **{**config.__dict__, "coach_model": model_id}
            )

    elif setting == "max_turns":
        val = questionary.text(
            f"Макс. попыток на шаг (текущее: {config.max_turns}):"
        ).ask()
        if val and val.isdigit():
            config = Config(**{**config.__dict__, "max_turns": int(val)})

    elif setting == "plan_file":
        val = questionary.text(
            f"Файл плана (текущий: {config.plan_file}):"
        ).ask()
        if val:
            config = Config(**{**config.__dict__, "plan_file": val})

    elif setting == "verbose":
        config = Config(**{**config.__dict__, "verbose": not config.verbose})

    elif setting == "autonomous":
        config = Config(**{**config.__dict__, "autonomous": not config.autonomous})

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
            "player_provider": config.player_provider,
            "coach_provider": config.coach_provider,
            "player_model": config.player_model,
            "coach_model": config.coach_model,
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
        coach_display = short_model_name(config.coach_model) if config.coach_model else "по умолчанию"
        player_display = short_model_name(config.player_model) if config.player_model else "по умолчанию"
        wd_display = config.working_dir.replace(str(Path.home()), "~")
        print(f"  [p] Player:        {config.player_provider} ({player_display})")
        print(f"  [c] Coach:         {config.coach_provider} ({coach_display})")
        print(f"  [1] Рабочая папка: {wd_display}")
        print(f"  [2] Файл плана:    {config.plan_file}")
        print(f"  [3] Макс. попыток: {config.max_turns}")
        print(f"  [4] Verbose:       {'вкл' if config.verbose else 'выкл'}")
        print(f"  [5] Автономный:    {'вкл' if config.autonomous else 'выкл'}")
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
            print("  Провайдеры: ccg, claude")
            val = input(f"  Player provider [{config.player_provider}]: ").strip()
            if val in ("ccg", "claude"):
                config = Config(**{**config.__dict__, "player_provider": val})
                if val == "claude":
                    print("  Модели: sonnet, opus, haiku")
                    model = input("  Player model [sonnet]: ").strip() or "sonnet"
                    config = Config(**{**config.__dict__, "player_model": model})
                else:
                    config = Config(**{**config.__dict__, "player_model": ""})
        elif answer == "c":
            print("  Провайдеры: ccg, claude")
            val = input(f"  Coach provider [{config.coach_provider}]: ").strip()
            if val in ("ccg", "claude"):
                config = Config(**{**config.__dict__, "coach_provider": val})
                if val == "claude":
                    print("  Модели: sonnet, opus, haiku")
                    model = input("  Coach model [sonnet]: ").strip() or "sonnet"
                    config = Config(**{**config.__dict__, "coach_model": model})
                else:
                    config = Config(**{**config.__dict__, "coach_model": ""})
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

        print()
