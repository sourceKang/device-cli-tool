from __future__ import annotations

from pathlib import Path


def configure_ssh_host_keys(
    client,
    paramiko,
    *,
    known_hosts_path: str | Path | None = None,
    allow_unknown_host_key: bool = False,
) -> None:
    """Configure a Paramiko client with fail-closed host-key verification."""
    client.load_system_host_keys()
    if known_hosts_path is not None:
        resolved = Path(known_hosts_path).expanduser()
        if not resolved.is_file():
            raise FileNotFoundError(f"known_hosts file does not exist: {resolved}")
        client.load_host_keys(str(resolved))

    policy = paramiko.WarningPolicy() if allow_unknown_host_key else paramiko.RejectPolicy()
    client.set_missing_host_key_policy(policy)
