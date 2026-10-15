# Family-labelled xPON remediation dry-run

`device-cli-xpon-plan` produces an auditable plan for the following requested sequence:

```text
show in remote xont unreg
show in remote xont summary
configure
interface xpon <slot>-<port>
inactive
register-method A
no inactive
exit
show in remote xont unreg
show in remote xont summary
```

The current implementation is intentionally plan-only. It does not create a transport, log in, or send commands to a device.

```powershell
device-cli-xpon-plan `
  --device-family IES4204 `
  --node-key NODE3 `
  --host 192.0.2.3 `
  --confirm-node NODE3 `
  --confirm-host 192.0.2.3 `
  --slot 3 `
  --port 16 `
  --report-dir reports/xpon-remediation
```

Safety properties:

- node and host confirmations must match exactly;
- host must be an IP literal;
- slot and port are bounded integers;
- the mutation sequence must match the fixed allowlist exactly;
- added commands, `register-method` values other than `A`, and command injection are rejected;
- JSON and HTML reports are redacted and contain no credentials;
- `--run-mutating` currently returns exit code 2 and `REFUSED_LIVE_EXECUTION_LOCKED`.

Live execution remains locked until the device-family-specific read-only command and parser for the original `register-method` and `inactive` state are verified. This evidence is required to build a trustworthy rollback. Prompt transitions for `configure` and interface mode must also be verified offline and on a lab device before enabling an executor.
