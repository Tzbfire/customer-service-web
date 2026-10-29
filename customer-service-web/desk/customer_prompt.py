"""Bundled customer-service prompt; never depends on runtime data or personal skills."""

from pathlib import Path

MAX_SYSTEM_PROMPT = 30000
DEFAULT_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "pdd_coating_service.md").read_text(
    encoding="utf-8"
)


def customer_prompt(settings):
    return settings.get("system_prompt", "").strip() or DEFAULT_SYSTEM_PROMPT.strip()
