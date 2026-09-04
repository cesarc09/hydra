import asyncio
import re

SKILLS_WRITE_LOCK = asyncio.Lock()

# No escape syntax: `{{{x}}}` still contains the marker `{{x}}`.
_MARKER_RE = re.compile(r"\{\{([a-z][a-z0-9_]*)\}\}")


def markers(common: str) -> set[str]:
    return set(_MARKER_RE.findall(common))


def render(common: str, slots: dict[str, str] | None) -> str:
    if slots is None:
        return common
    return _MARKER_RE.sub(lambda match: slots[match.group(1)], common)


def validate(common: str, variants: dict[str, dict[str, str]]) -> None:
    required = markers(common)
    for harness in sorted(variants):
        missing = sorted(required - variants[harness].keys())
        if missing:
            raise ValueError(f"Harness {harness!r} is missing slot {missing[0]!r}")
