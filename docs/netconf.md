# NETCONF test tool

`device-cli-netconf-test` tests a NETCONF/SSH connection and runs `<get>`/`<get-config>` (read-only) and `edit-config` (mutating, opt-in) against a device.

`connect`/`get`/`get-config` have been verified against a real NeoX NXC400 (firmware `103ACKG0b3`): capability exchange, `get-config(running)`, and a `<get>` with a device-specific subtree filter (`zyxel-interfaces-pon-cfg`) all succeeded. `edit-config`'s live path (candidate + validate + confirmed-commit) is architecturally complete and offline-tested; treat its exact behavior against a specific device as something to verify on that device before relying on it, the same way `connect`/`get`/`get-config` were verified here.

## Installation

NETCONF support is an optional extra so the core package stays lean for projects that never touch NETCONF:

```powershell
python -m pip install "device-cli-tool[netconf] @ git+https://github.com/sourceKang/device-cli-tool.git@main"
device-cli-netconf-test --help
```

`ncclient` handles the RFC 6242 SSH framing and RFC 6241 rpc-reply/rpc-error parsing; it is not implemented by hand in this project the way Telnet is.

## Read-only operations

```powershell
$env:CLI_TOOL_NETCONF_PASSWORD="your-password"
device-cli-netconf-test connect --host 192.0.2.10 --username admin `
  --host-key-fingerprint "SHA256:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
device-cli-netconf-test get --host 192.0.2.10 --username admin --include-output `
  --host-key-fingerprint "SHA256:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
device-cli-netconf-test get-config --host 192.0.2.10 --username admin --datastore running --include-output `
  --host-key-fingerprint "SHA256:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

- `connect` performs the hello/capability exchange, reports `session_id`, `server_capabilities` and the negotiated `host_key_fingerprint`, and closes the session immediately.
- `get`/`get-config` accept an optional `--filter-file` (a full `<filter>...</filter>` element — subtree or XPath). A fragment pulled out of a larger `<rpc>` document loses the namespace it inherited from its parent; declare it explicitly, e.g. `<filter xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">...</filter>`, or the device replies with `Missing XML namespace`. `get-config` accepts `--datastore {running,candidate,startup}` (default `running`).
- A `<get>`/`<get-config>` reply for a device with live PON/xPON state can take longer than the 15s default; raise `--timeout` if you see `TimeoutExpiredError` rather than assuming the request failed. Reading PM/statistics history for one active PON port took ~12s against a real NXC400.
- Raw NETCONF XML is only included in stdout/report with `--include-output`, truncated at `--max-output-chars` (default 20000). `get-config(running)` can return several hundred KB, including `ietf-keystore` key material — always redacted (see below), but still worth using `--filter-file` to scope the request instead of pulling the entire running datastore when you don't need it.
- Reports are redacted before being printed or written: XML tags such as `<password>`, `<hashed-password>`, `<community>`, `<pre-shared-key>`, `<secret>`, `<token>`, `<public-key>`, `<private-key>`, `<cleartext-private-key>`, `<encrypted-private-key>`, `<cleartext-symmetric-key>`, `<encrypted-symmetric-key>` are masked, in addition to the existing key/text redaction rules.

## Host-key verification

Host-key verification is a **pinned SHA256 fingerprint**, not `ncclient`'s built-in `hostkey_verify`. Pass `--host-key-fingerprint` (as `ssh-keygen -lf` prints it, with or without the `SHA256:` prefix) after verifying it through a trusted channel — console access, vendor documentation, or a separately-verified `ssh-keyscan`. Without a fingerprint, the tool refuses to send any RPC unless `--allow-unknown-host-key` is explicit; that flag is an escape hatch and must never be used in CI.

**Why not `ncclient`'s own `hostkey_verify=True`:** when `ncclient` finds a `known_hosts` entry for the host, it overwrites paramiko's preferred host-key algorithm list with whichever key *type* is on file (`ncclient/transport/ssh.py:305-312`). If that type is `ssh-rsa`, paramiko negotiation is pinned to `ssh-rsa` — and paramiko 5.x has removed the SHA-1 `ssh-rsa` signature scheme entirely (confirmed against paramiko's own `Transport._key_info`, which no longer lists it). An RSA host key still works fine over `rsa-sha2-256`/`rsa-sha2-512`, which paramiko does support, but only if nothing has locked negotiation to the legacy type first. `NetconfClient` therefore connects with `hostkey_verify=False` (so paramiko negotiates normally) and verifies the returned key against the pinned fingerprint itself, **after the key exchange and before user authentication** — a pre-auth hook on ncclient's `SSHSession._auth()` — so the password is never sent to a server whose key does not match the pin. (v0.7.0 checked the key only after password authentication; fixed in 0.7.1.) The key is checked once more on the established session before any RPC, as defense in depth. **Do not add a NETCONF host's key to a plain OpenSSH `known_hosts` file and pass it to `ncclient` directly** — that is exactly the path that reintroduces this failure for an RSA-keyed server.

## `edit-config`: dry-run by default, candidate + confirmed-commit when live

```powershell
# Preview only — never connects, regardless of device reachability.
device-cli-netconf-test edit-config `
  --host 192.0.2.3 --node-key NODE1 --confirm-node NODE1 --confirm-host 192.0.2.3 `
  --edit-config-file edit.xml

# Live execution.
device-cli-netconf-test edit-config `
  --host 192.0.2.3 --username admin `
  --node-key NODE1 --confirm-node NODE1 --confirm-host 192.0.2.3 `
  --edit-config-file edit.xml `
  --host-key-fingerprint "SHA256:..." `
  --run-mutating `
  --confirm-timeout-seconds 30 `
  --verify-filter-file verify-filter.xml
```

Without `--run-mutating`: identical to before, prints a `DRY_RUN_READY` plan and never opens a connection.

With `--run-mutating`: connects and executes `lock(candidate)` → `edit-config(candidate)` → `validate(candidate)` → `commit(confirmed=True, timeout=--confirm-timeout-seconds)` → optional `<get>` against `--verify-filter-file` → `unlock(candidate)`, in that order, on one session. Two things are structural, not conventions the caller has to remember:

- **It always targets `candidate`.** There is no flag that lets it write `running` directly — `--datastore` is rejected outright for `edit-config`. `--edit-config-file` must contain the `<config>...</config>` body only (matching what `ncclient`'s `edit_config()` expects); a full `<rpc><edit-config>...` envelope with its own `<target>` won't parse, and even if it did, the target it declares is ignored.
- **The commit is not finalized unless `--finalize-commit` is explicit.** Per RFC 6241 confirmed-commit semantics (device-confirmed capability: `:confirmed-commit:1.1`), if the confirming commit is never sent, the device reverts `running` to its pre-commit state once `--confirm-timeout-seconds` elapses (default 30s, bounded 5–600) or the session closes. This makes rollback a property of every live call by default, not something proven once and then trusted forever — re-verify it on a given device/firmware before depending on it, the same way the read-only path here was verified against a real NXC400 before being trusted.

**What a non-finalized commit means in practice.** Without `--finalize-commit` (and without `persist`, see below), RFC 6241 §8.4 makes the device revert the confirmed commit as soon as the session that issued it closes — and `execute_confirmed_commit()` closes its session right after `unlock`, at the end of the call. So:

- the change is only visible inside the call, i.e. to `--verify-filter-file`'s `<get>`; **other interfaces (CLI, SNMP, Web UI) never see it**, so this mode cannot drive a cross-interface check;
- `--confirm-timeout-seconds` / `confirm_timeout` has no practical effect, because session close always comes first.

`confirm_timeout` is sent as `<confirm-timeout>` text in whole seconds (≥ 1; the CLI bounds it to 5–600). Fractional or sub-second values are rejected rather than truncated. (v0.7.0 passed an `int`, which ncclient cannot serialize — every live commit failed with `TypeError` and fell back to `discard-changes` + `unlock`; fixed in 0.7.1.)

**Persisted confirmed commit (Python API only).** To let other interfaces observe a change while keeping the automatic revert, pass `persist=<token>` (requires `:confirmed-commit:1.1`, checked fail-closed before any RPC):

```python
token = f"neox-{uuid.uuid4()}"            # unguessable; anyone holding it can confirm/cancel
client.execute_confirmed_commit(config_xml=config_xml, confirm_timeout=120, persist=token)
# running now holds the change, across sessions, for up to 120s: read it via CLI/SNMP/Web here
client.cancel_persisted_commit(persist_id=token)    # revert now (normal test teardown)
# or: client.confirm_persisted_commit(persist_id=token) to make it permanent
```

If the caller crashes before cancelling, the device still reverts when `confirm_timeout` elapses, so pick the shortest window the cross-interface check needs. `finalize=True` together with `persist` sends `<commit><persist-id>token</persist-id></commit>`. There is no CLI flag for `persist` yet.

**Capability check (fail-closed).** Right after the hello exchange and before any `lock`/`edit-config`, `execute_confirmed_commit()` checks the server's advertised capabilities. It requires both `urn:ietf:params:netconf:capability:candidate:1.0` (`:candidate`) and `urn:ietf:params:netconf:capability:confirmed-commit:1.0` or `:1.1` (`:confirmed-commit`). If either is missing it raises `NetconfCapabilityError` (its `.missing` lists e.g. `(":candidate",)`), closes the session and sends no RPC at all. It never falls back to `:writable-running`. The CLI reports this as `"result": "REFUSED_MISSING_CAPABILITY"`, `"error_type": "NetconfCapabilityError"` and `"missing_capabilities": [":candidate"]`, with exit code 1. Query parameters on an advertised URI (`?...`) are ignored when matching. Run `connect` first to see what a device advertises.

On any failure before a successful commit, `discard-changes` runs on `candidate` before `unlock`, so a rejected `validate` or `edit-config` never leaves a half-applied candidate sitting on the device. `config_xml` itself is **not validated against an allowlist** the way xPON's fixed command sequence is — NETCONF payloads are arbitrary YANG data, so the safety boundary here is procedural (candidate + the device's own `validate` RPC + a commit that reverts unless confirmed), not content-based. This is a deliberate trust boundary: the device's own YANG validation is what catches a structurally invalid config, not this tool.

## External integration

`device-cli-tool` is shared by several projects. Each consumer pins what it uses; do not rely on `main`.

### NeoxAutoTest

Pins a git tag in `pyproject.toml` and adds the `netconf` extra only when it needs NETCONF:

```toml
dependencies = [
  "device-cli-tool[netconf] @ git+https://github.com/sourceKang/device-cli-tool.git@v0.7.1",
]
```

After changing the pin, verify the tag resolves to the expected commit before trusting it (`git ls-remote https://github.com/sourceKang/device-cli-tool.git refs/tags/v0.7.1`).

### EMS web auto

Installs from a sibling checkout (`../device-cli-tool`) via `scripts/setup_cli_tool.ps1`, falling back to a pinned commit when no local checkout exists, then checks that the installed distribution version equals the script's `ExpectedVersion`. When the local checkout moves to a new release, update `ExpectedVersion` (and the fallback commit) in that script in the same change; otherwise its verification fails on the version mismatch. EMS web auto only uses the read-only CLI preflight, so it does not need the `netconf` extra.

### Python API (minimal)

```python
import os
from cli_tool.transport.netconf import NetconfCapabilityError, NetconfClient

client = NetconfClient(
    host="192.0.2.10",
    username="admin",
    password=os.environ["CLI_TOOL_NETCONF_PASSWORD"],
    expected_host_key_fingerprint="SHA256:...",
)

hello = client.connect_test()             # .session_id, .server_capabilities, .host_key_fingerprint
state = client.get(filter_xml=filter_xml)  # .xml is the raw rpc-reply; redact before logging
running = client.get_config(datastore="running", filter_xml=filter_xml)

try:
    result = client.execute_confirmed_commit(
        config_xml=config_xml,             # <config>...</config> body only
        verify_filter_xml=filter_xml,
        confirm_timeout=30,
        finalize=False,                    # default: device reverts running when this call's session closes
    )
except NetconfCapabilityError as error:
    print("unsupported device:", error.missing)
```

`.xml` fields are unredacted; pass them through `cli_tool.reporting.redaction.redact_text()` before writing them to any report or log. `NetconfClient.edit_config()` is the unguarded single RPC described under Boundaries; integrations should use `execute_confirmed_commit()` for mutation.

### Compatibility promise

- Within 0.x, the names and keyword arguments of `NetconfClient`, `NetconfCapabilityError`, `NetconfHostKeyError` and the result dataclasses shown above are kept; additions are backwards compatible. Any breaking change is called out in `CHANGELOG.md` and ships with a minor-version bump.
- The safety defaults are not relaxed in a minor release: candidate-only mutation, the capability fail-closed check, auto-reverting confirmed commit unless finalized, and pinned host-key verification.
- The CLI's JSON report keys may gain fields; existing keys are not renamed within 0.x.

## Boundaries

`cli_tool.transport.netconf.NetconfClient.edit_config()` sends a single, unguarded `edit-config` with no lock/validate/commit around it. The CLI never calls it directly — live mutation always goes through `execute_confirmed_commit()`. There is no multi-device or session-pooling support; each invocation opens and closes one session.
