"""Handling for untrusted text (API titles/descriptions, comments, web pages) per OWASP LLM01.

Untrusted text is cleaned (control and bidirectional-override characters removed, length capped)
and, when shown to an agent, wrapped in ``<untrusted_data>`` blocks whose closing tag cannot be
forged from inside. Agent definitions instruct agents to treat block contents as data only.
"""

from __future__ import annotations

import re
import unicodedata

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# bidi overrides/isolates and zero-width chars used for spoofing or hidden prompts
_INVISIBLE_RANGES = (
    (0xE0000, 0xE007F),  # Unicode tag characters: invisible text used to smuggle prompts
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x2064),
    (0x2066, 0x2069),
    (0xFEFF, 0xFEFF),
)
_INVISIBLE = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in _INVISIBLE_RANGES) + "]")


def clean_text(text: object, max_len: int = 500) -> str:
    """Normalise to NFC, strip control/invisible characters, collapse whitespace, cap length."""
    s = unicodedata.normalize("NFC", str(text or ""))
    s = _INVISIBLE.sub("", _CONTROL.sub(" ", s))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]


def wrap_untrusted(text: str, source: str = "external") -> str:
    """Wrap text for an agent prompt; any embedded tag-like sequences are defused."""
    safe = clean_text(text, 4000).replace("<", "‹").replace(">", "›")
    src = re.sub(r"[^a-z0-9_.:-]", "", source.lower())[:40] or "external"
    return f'<untrusted_data source="{src}">{safe}</untrusted_data>'
