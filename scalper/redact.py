"""Strip secret-shaped strings before anything is written or printed.

data/health.json and data/alert_log.json are committed to a PUBLIC
repository and both store error text. Today that text is harmless. The
day a provider raises `401 for https://api/...?key=sk-ant-...`, or a
traceback carries the ntfy topic, the secret goes into the commit and
stays in the history forever.

Redaction belongs at the boundary where text is stored, not at each
call site, because the call site that forgets is exactly the one that
will see the interesting error.
"""
from __future__ import annotations

import os
import re

_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)\b(api[-_]?key|token|secret|password)\b\s*[=:]\s*\S+"),
    re.compile(r"ntfy\.sh/[A-Za-z0-9_\-]{6,}"),
    re.compile(r"https?://[^\s]*[?&](?:key|token|apikey|auth)=[^\s&]+"),
)

# Values we know are secret because the environment says so.
_ENV_KEYS = ("ANTHROPIC_API_KEY", "NTFY_TOPIC", "GITHUB_TOKEN",
             "TWILIO_AUTH_TOKEN", "TWILIO_ACCOUNT_SID", "WEBSHARE_PASS",
             "YT_PROXY_URL", "WEBSHARE_USER")


def scrub(text):
    """Return text with anything secret-shaped replaced."""
    if not isinstance(text, str) or not text:
        return text
    out = text
    # Exact environment values first: they are certain, and a short
    # topic string would not match any generic pattern.
    for key in _ENV_KEYS:
        val = (os.environ.get(key) or "").strip()
        if len(val) >= 6:
            out = out.replace(val, f"<{key}>")
    for pat in _PATTERNS:
        out = pat.sub("<redacted>", out)
    return out
