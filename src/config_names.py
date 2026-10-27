"""Короткие имена моделей для HUD и сводок (вынесено из src/config.py, план 08-6)."""

from __future__ import annotations


def short_model_name(model: str) -> str:
    """Get short display name from a model string."""
    m = model.lower()
    if not m or m == "default":
        return "DEFAULT"
    if m == "o3":
        return "o3"
    if m == "o4-mini":
        return "o4-mini"
    if "contributor-free" in m or "spark-1.3-contributor" in m:
        return "SPARK$0"
    if "muse-spark" in m or "spark-1" in m:
        return "SPARK"
    if "composer" in m:
        return "COMPOSER"
    if "grok" in m:
        return "GROK"
    if "fable" in m:
        return "FABLE"
    if "opus" in m:
        return "OPUS"
    if "sonnet" in m:
        return "SONNET"
    if "haiku" in m:
        return "HAIKU"
    if "astra" in m:
        return "ASTRA"
    if "gpt-5.6-sol" in m or m.endswith("-sol"):
        return "SOL"
    if "gpt-5.6-terra" in m or m.endswith("-terra"):
        return "TERRA"
    if "gpt-5.6-luna" in m or m.endswith("-luna"):
        return "LUNA"
    if "gpt-5.6" in m:
        return "GPT-5.6"
    if "gpt-5.5" in m:
        return "GPT-5.5"
    if "gpt-5.4" in m:
        return "GPT-5.4"
    if "glm-5.1" in m:
        return "GLM-5.1"
    if "glm-4.7" in m:
        return "GLM-4.7"
    if "glm-5-turbo" in m:
        return "GLM-5-T"
    if "mimo" in m:
        return "MIMO"
    if "minimax" in m:
        return "MINIMAX"
    if "glm" in m:
        glm_pos = m.find("glm")
        rest = m[glm_pos + 3 :].lstrip("-")
        ver = rest.split("-")[0]
        return f"GLM-{ver}" if ver else "GLM"
    if "kimi" in m:
        return "KIMI"
    if "gemini-3.1-pro" in m:
        return "GEMINI-3.1-PRO"
    if "gemini-3.8-flash" in m:
        return "GEMINI-3.8-FL"
    if "gemini-3.5-flash-lite" in m:
        return "GEMINI-3.5-LITE"
    if "gemini-3.5-flash" in m:
        return "GEMINI-3.5-FL"
    if "gemini-3-flash" in m:
        return "GEMINI-3-FL"
    if "gemini-3.1-flash-lite" in m:
        return "GEMINI-3.1-LITE"
    if "gemini-2.5-pro" in m:
        return "GEMINI-2.5-PRO"
    if "gemini-2.5-flash" in m:
        return "GEMINI-2.5-FL"
    if "gemini" in m:
        return "GEMINI"
    return model.split("/")[-1].upper()[:10]
