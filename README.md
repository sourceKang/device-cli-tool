# Device CLI Tool

Reusable multi-device CLI automation and read-only verification tool.

## Current scope

- Read-only command catalogs under `configs/cli_tool/`.
- Single-device generic `show_version` smoke workflow.
- SSH transport based on Paramiko.
- SSH connection retry for transient failures, with exponential backoff and no authentication retry.
- Serial console transport based on pySerial.
- Redacted JSON report output.
- Redacted and size-limited `--include-output` report output.
- Optional `--env-node` integration for SSH or serial settings from a consuming project's `node_target.cli` config.

The current operational target is one device per CLI invocation. Multi-device concurrent execution is intentionally out of scope for now.

## Installation

After the repository is published, install it into any project's virtual environment:

```powershell
python -m pip install "device-cli-tool @ git+https://github.com/sourceKang/device-cli-tool.git@main"
device-cli-smoke --help
```

See `docs/integration.md` for portable target config and optional consuming-project integration.

## Offline validation

```powershell
python -m compileall cli_tool tools\cli_tool_readonly_smoke.py
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
