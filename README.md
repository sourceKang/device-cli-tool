# Device CLI Tool

Reusable multi-device CLI automation and read-only verification tool.

## Current scope

- Read-only command catalogs under `configs/cli_tool/`.
- Single-device generic `show_version` smoke workflow.
- SSH transport based on Paramiko.
- Strict SSH host-key verification by default, with optional explicit `known_hosts`.
- Root-bounded SFTP read-only list/stat/exists/download operations.
- Verified Explicit/Implicit FTPS and explicit opt-in plaintext FTP read-only transfers.
- Prompt-aware SSH and serial command completion with a bounded timeout.
- SSH connection retry for transient failures, with exponential backoff and no authentication retry.
- Serial console transport based on pySerial.
- Explicit opt-in Telnet read-only transport for isolated legacy labs.
- Redacted JSON report output.
- Redacted and size-limited `--include-output` report output.
- Empty CLI output always fails verification, including commands without expected tokens.
- Fail-closed `show lc st` fixed-width table parser and JSON-safe line-card snapshot workflow.
- Optional `--env-node` integration for SSH or serial settings from a consuming project's `node_target.cli` config.

The current operational target is one device per CLI invocation. Multi-device concurrent execution is intentionally out of scope for now.

## Installation

After the repository is published, install it into any project's virtual environment:

```powershell
python -m pip install "device-cli-tool @ git+https://github.com/sourceKang/device-cli-tool.git@main"
device-cli-smoke --help
device-cli-transfer --help
```

See `docs/integration.md` for consuming-project integration and `docs/file_transfer.md` for SFTP safety and usage.

## Offline validation

```powershell
python -m compileall cli_tool tools
python -m pytest tests
```

## Read-only smoke

Generic/manual mode:

```powershell
$env:CLI_TOOL_SSH_PASSWORD="your-password"
device-cli-smoke --node-key node1 --host 192.0.2.10 --username admin
```

Optional `config_loader` integration mode:

```powershell
device-cli-smoke --env-node node1 --auth-profile default
```

Unknown SSH host keys are rejected by default. Use `--known-hosts` after verifying the device fingerprint through a trusted channel. `--allow-unknown-host-key` is an explicit troubleshooting escape hatch and should never be used in CI.

The consuming project may define node-specific CLI settings under its target config:

```yaml
nodes:
  NODE1:
    device_ip: "192.0.2.10"
    cli:
      transport: "serial"
      serial_port: "COM5"
      baudrate: 115200
      timeout: 15
      username: "admin"
      password_env: "CLI_TOOL_SERIAL_PASSWORD"
      catalog: "builtin:ies52xx"
      default_command: "show_version"
```

Passwords remain in the configured environment variable or the consuming project's auth provider. Command-line values override `node_target.cli`, which overrides tool defaults.

Serial console mode:

```powershell
$env:CLI_TOOL_SERIAL_PASSWORD="your-password"
device-cli-smoke --transport serial --serial-port COM5 --username admin --password-env CLI_TOOL_SERIAL_PASSWORD
```

Portable config mode for projects without a compatible `config_loader`:

```powershell
device-cli-smoke --target-config configs/device_cli_targets.yaml --target lab-ies
```

## Read-only SFTP

```powershell
$env:CLI_TOOL_SFTP_PASSWORD="your-password"
device-cli-transfer download `
  --host 192.0.2.10 `
  --username admin `
  --known-hosts "$HOME\.ssh\known_hosts" `
  --remote-root /var/log `
  --remote-path messages `
  --local-path artifacts\messages.log
```

SFTP is a separate file-transfer backend, not an interactive CLI transport. It does not support upload, delete, rename, or recursive download. Paths and target identifiers are hashed in stdout and reports unless `--include-paths` is explicit.

## Legacy Telnet

Telnet is available only with the explicit `--allow-insecure-telnet` flag. It reuses prompt-aware command completion and read-only catalogs, but credentials and output remain plaintext on the network. See `docs/telnet.md`.

## FTPS and FTP

The `device-cli-transfer` command supports verified Explicit FTPS, verified Implicit FTPS, and plaintext FTP with the mandatory `--allow-insecure-ftp` flag. FTPS protects the control and data channels; certificate verification is strict by default. See `docs/ftp_ftps.md`.
