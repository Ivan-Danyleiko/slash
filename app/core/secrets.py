from __future__ import annotations

import re

_REDACTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(bearer\s+)[^\s,;]+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(token\s+)[^\s,;]+"), r"\1***REDACTED***"),
    (re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+"), r"\1***REDACTED***"),
    (re.compile(r"0x[a-fA-F0-9]{64}"), "***REDACTED_PRIVATE_KEY***"),
]


def redact_text(value: str, *, max_len: int = 500) -> str:
    text = str(value or "")
    for pattern, repl in _REDACTION_PATTERNS:
        text = pattern.sub(repl, text)
    if len(text) > max_len:
        text = text[:max_len]
    return text

