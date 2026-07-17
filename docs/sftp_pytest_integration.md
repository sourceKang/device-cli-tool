# Pytest integration for read-only SFTP

Copy the fixture pattern from `examples/pytest_sftp_fixture.py` into the consuming project's `conftest.py`, or import the client directly.

The fixture is session-scoped, but it returns a factory. Each test or preflight owns the connection lifetime explicitly:

```python
def test_remote_inventory_exists(sftp_client_factory):
    client = sftp_client_factory(
        host="192.0.2.10",
        username="admin",
        remote_root="/var/lib/device",
        known_hosts="configs/known_hosts",
    )
    with client:
        assert client.exists("inventory.json")
```

Keep host, username, remote root, and the known-hosts path in the consuming project's environment configuration. Keep passwords and private-key passphrases in environment variables or an external secret provider.

Do not enable `allow_unknown_host_key` in CI. Verify the host fingerprint through a trusted channel before committing a sanitized public host key or provisioning a private CI known-hosts file.
