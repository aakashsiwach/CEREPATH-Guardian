from __future__ import annotations

import re

_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)[^\s,;]+"),
    re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|passwd|client_secret|aws_secret_access_key|aws_access_key_id)\b\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)(\b(?:GEMINI_API_KEY|BYNARA_API_KEY|CEREPATH_OWNER_TOKEN|BROWSER_INTERNAL_TOKEN|GITHUB_TOKEN|AWS_SECRET_ACCESS_KEY|AWS_ACCESS_KEY_ID)\s*=\s*)[^\s]+"),
    re.compile(r"(?i)(cookie\s*:\s*)[^\r\n]+"),
    re.compile(r"(?i)(set-cookie\s*:\s*)[^\r\n]+"),
    re.compile(r"(?i)(://[^:/@\s]+:)[^@/\s]+(@)"),
    re.compile(r"(?i)([?&](?:access[_-]?token|refresh[_-]?token|api[_-]?key|client[_-]?secret|token|key|secret|password)=)[^&\s]+"),
]


def redact(text: str) -> str:
    result = text
    for pattern in _PATTERNS:
        result = pattern.sub(r"\1[REDACTED]", result)
    return result
