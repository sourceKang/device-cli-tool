# Telnet read-only CLI transport

Telnet support exists only for legacy devices in an isolated lab. Telnet sends usernames, passwords, commands, and output as plaintext.

## Mandatory opt-in

Every invocation must include `--allow-insecure-telnet`. The flag cannot be enabled from portable target YAML or a consuming project's node config.

```powershell
$env:CLI_TOOL_TELNET_PASSWORD="your-password"
device-cli-smoke `
  --transport telnet `
  --allow-insecure-telnet `
  --node-key legacy-node `
  --host 192.0.2.10 `
  --username admin `
  --password-env CLI_TOOL_TELNET_PASSWORD `
  --telnet-port 23 `
  --telnet-timeout 15
```

## Behavior

- Uses a bounded monotonic timeout for connect, login, and command completion.
- Waits for an established CLI prompt; it does not use a fixed sleep or idle gap.
- Supports common username/password prompts, yes/no confirmation, and the existing pager pattern.
- Refuses Telnet optional features during RFC 854 negotiation.
- Uses only catalog commands marked `readonly: true`.
- Empty command output remains empty and therefore fails the shared verifier.
- Reports record `security: insecure_plaintext`.
- Full output is excluded unless `--include-output` is explicit.

## Boundaries

Telnet has no certificate or host-key verification. Do not route it over the public internet or a shared untrusted network. The tool does not provide Telnet configuration mode, mutation, upload, or file transfer.
