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


def test_redact_text_masks_sensitive_netconf_xml_tags():
    xml = (
        "<rpc-reply><data>"
        "<username>admin</username>"
        "<password>hunter2</password>"
        "<snmp><a:community xmlns:a=\"urn:example\">public</a:community></snmp>"
        "<pre-shared-key>abc123</pre-shared-key>"
        "</data></rpc-reply>"
    )

    redacted = redact_text(xml)

    assert "hunter2" not in redacted
    assert "public" not in redacted
    assert "abc123" not in redacted
    assert "admin" in redacted
    assert "<password>***REDACTED***</password>" in redacted
    assert "<a:community xmlns:a=\"urn:example\">***REDACTED***</a:community>" in redacted
    assert "<pre-shared-key>***REDACTED***</pre-shared-key>" in redacted


def test_redact_text_masks_ietf_keystore_key_material():
    # Shaped like the ietf-keystore data an actual NETCONF get-config(running) returned.
    xml = (
        "<keystore xmlns='urn:ietf:params:xml:ns:yang:ietf-keystore'><asymmetric-keys>"
        "<asymmetric-key><name>genkey</name><algorithm>rsa2048</algorithm>"
        "<public-key>FAKEPUBLICKEYMATERIAL</public-key>"
        "<cleartext-private-key>FAKEPRIVATEKEYMATERIAL</cleartext-private-key>"
        "</asymmetric-key></asymmetric-keys></keystore>"
        "<user><name>admin</name><hashed-password>FAKEHASH</hashed-password></user>"
    )

    redacted = redact_text(xml)

    assert "FAKEPUBLICKEYMATERIAL" not in redacted
    assert "FAKEPRIVATEKEYMATERIAL" not in redacted
    assert "FAKEHASH" not in redacted
    assert "genkey" in redacted
    assert "<public-key>***REDACTED***</public-key>" in redacted
    assert "<cleartext-private-key>***REDACTED***</cleartext-private-key>" in redacted
    assert "<hashed-password>***REDACTED***</hashed-password>" in redacted
