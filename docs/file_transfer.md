# Read-only SFTP

SFTP is a file-transfer capability. It is intentionally separate from interactive CLI transports such as SSH shell, serial console, and Telnet.

## Supported operations

- `list`: list metadata inside an allowed remote root.
- `stat`: read metadata without following the final symlink.
- `exists`: test whether a path exists.
- `download`: download one regular, non-symlink file.

Upload, delete, rename, chmod, mkdir, and recursive download are not supported.

## Host-key verification

SSH and SFTP reject unknown host keys by default. The client loads system host keys and can load an additional OpenSSH `known_hosts` file with `--known-hosts`.

Verify the device fingerprint through a trusted channel before adding it to `known_hosts`. Do not treat a key obtained from an unauthenticated network scan as verified.

`--allow-unknown-host-key` is an explicit local troubleshooting escape hatch. It emits a warning, is recorded in the report, does not persist the key, and should never be used in CI.

## Authentication

Password authentication reads the password from an environment variable:

```powershell
$env:CLI_TOOL_SFTP_PASSWORD="your-password"
device-cli-transfer exists `
  --host 192.0.2.10 `
  --username admin `
  --remote-root /var/log `
  --remote-path messages
```

Private-key authentication is selected with `--private-key`. An encrypted key passphrase must also come from an environment variable:

```powershell
$env:CLI_TOOL_SFTP_KEY_PASSPHRASE="your-passphrase"
device-cli-transfer exists `
  --host 192.0.2.10 `
  --username admin `
  --private-key "$HOME\.ssh\id_ed25519" `
  --private-key-passphrase-env CLI_TOOL_SFTP_KEY_PASSPHRASE `
  --known-hosts "$HOME\.ssh\known_hosts" `
  --remote-root /var/log `
  --remote-path messages
```

The client requires exactly one authentication method. It disables SSH agent discovery and implicit local key discovery so CI behavior remains deterministic.

## Examples

List a directory under a bounded remote root:

```powershell
device-cli-transfer list `
  --host 192.0.2.10 `
  --username admin `
  --known-hosts "$HOME\.ssh\known_hosts" `
  --remote-root /var/log `
  --remote-path . `
  --timeout 15
```

Download one file with a 10 MiB limit:

```powershell
device-cli-transfer download `
  --host 192.0.2.10 `
  --username admin `
  --remote-root /var/log `
  --remote-path messages `
  --local-path artifacts\messages.log `
  --max-download-bytes 10485760
```

The destination is committed only after the complete file is received, its byte count matches remote metadata, the remote size/type/modified-time metadata remains stable, and SHA-256 has been calculated. Existing destinations are rejected unless `--overwrite` is explicit.

## Path and report safety

- `remote_root` must be an absolute POSIX path.
- Parent traversal, NUL, backslashes, and absolute paths outside the configured root are rejected.
- Downloads accept only regular files and reject symlinks.
- Default maximum download size is 50 MiB.
- Partial files use a temporary `.part` name and are removed after failure.
- File contents are never written to stdout or JSON reports.
- Host, username, remote paths, and local paths are hashed in stdout and reports by default.
- `--include-paths` reveals those identifiers and should only be used in a trusted environment.
- Transfer reports declare `schema_version: 1` and record the authentication method and timeout without recording credentials.

Reports are written to `reports/cli-tool-transfer/` unless `--no-report` is specified.

For consuming-project pytest integration, see `docs/sftp_pytest_integration.md`.
