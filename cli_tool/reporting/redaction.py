from __future__ import annotations

import re
from typing import Any


SENSITIVE_KEYS = {
    "password",
    "passwd",
    "sessionid",
    "token",
    "authorization",
    "devkey",
    "community",
    "snmpcommunity",
}
MAC_ADDRESS = re.compile(r"\b[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}\b")
SERIAL_FIELD = re.compile(r"(?im)^(\s*(?:serial(?:\s+number)?|chassis\s+serial)\s*:\s*).+$")
HOSTNAME_FIELD = re.compile(r"(?im)^(\s*hostname\s*:\s*).+$")
CONTACT_FIELD = re.compile(r"(?im)^(\s*contact\s*:\s*).+$")
LOCATION_FIELD = re.compile(r"(?im)^(\s*location\s*:\s*).+$")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                redacted[key] = "***REDACTED***"
            else:
                redacted[key] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(value: str) -> str:
    value = MAC_ADDRESS.sub("***MAC***", value)
    value = SERIAL_FIELD.sub(r"\1***REDACTED***", value)
    value = HOSTNAME_FIELD.sub(r"\1***REDACTED***", value)
    value = CONTACT_FIELD.sub(r"\1***REDACTED***", value)
    return LOCATION_FIELD.sub(r"\1***REDACTED***", value)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.replace("_", "").replace("-", "").lower()
    return normalized in SENSITIVE_KEYS
