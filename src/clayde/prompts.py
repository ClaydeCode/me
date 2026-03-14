"""Prompt template utilities and shared comment helpers."""

from pathlib import Path

from jinja2 import Environment, StrictUndefined

PROMPTS_DIR = Path(__file__).parent / "prompts"

_env = Environment(undefined=StrictUndefined)


def render_template(name: str, **ctx) -> str:
    """Render a Jinja2 template from the prompts directory."""
    template_src = (PROMPTS_DIR / name).read_text()
    return _env.from_string(template_src).render(**ctx)


def collect_comments_after(comments: list, anchor_id: int) -> str:
    """Return formatted text of visible comments posted after anchor_id.

    Comments are formatted as '@login:\\nbody' and joined with '---' separators.
    Returns '(none)' when there are no comments after the anchor.
    """
    past_anchor = False
    parts = []
    for comment in comments:
        if comment.id == anchor_id:
            past_anchor = True
            continue
        if past_anchor:
            parts.append(f"@{comment.user.login}:\n{comment.body}")
    return "\n---\n".join(parts) or "(none)"
