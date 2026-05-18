"""Prompt template utilities."""

from pathlib import Path

from jinja2 import Environment, StrictUndefined

PROMPTS_DIR = Path(__file__).parent / "prompts"

_env = Environment(undefined=StrictUndefined)


def render_template(name: str, **ctx) -> str:
    """Render a Jinja2 template from the prompts directory."""
    template_src = (PROMPTS_DIR / name).read_text()
    return _env.from_string(template_src).render(**ctx)
