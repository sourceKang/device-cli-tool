from __future__ import annotations

from cli_tool.reporting.redaction import redact, redact_text


def test_redact_masks_sensitive_keys_and_nested_values():
    payload = {
        "password": "secret",
        "session_id": "abc",
        "nested": {"devKey": "key", "snmp-community": "public"},
    }

    assert redact(payload) == {
        "password": "***REDACTED***",
        "session_id": "***REDACTED***",
        "nested": {"devKey": "***REDACTED***", "snmp-community": "***REDACTED***"},
    }


def test_redact_text_masks_common_system_information_fields():
    output = "\n".join(
        [
            "Hostname: NXC400",
            "Location: BackupLocationInfo",
            "Contact: Jane",
            "MAC address: F8:D4:A3:C9:E1:B0",
            "Serial number: S230Z10023204",
            "Chassis serial: S250Z31000077",
        ]
    )

    redacted = redact_text(output)

    assert "NXC400" not in redacted
    assert "BackupLocationInfo" not in redacted
    assert "Jane" not in redacted
    assert "F8:D4:A3:C9:E1:B0" not in redacted
    assert "S230Z10023204" not in redacted
    assert "S250Z31000077" not in redacted
    assert "Hostname: ***REDACTED***" in redacted
    assert "MAC address: ***MAC***" in redacted
