# FTPS and FTP read-only transfers

Version 0.5.1 supports Explicit FTPS, Implicit FTPS, opt-in plaintext FTP, and strict opt-in UNIX legacy metadata for servers without MLSD/MLST.

## Protocol selection

| Protocol | CLI value | Default port | Security |
| --- | --- | ---: | --- |
| SFTP | `sftp` | 22 | SSH host-key verification |
| Explicit FTPS | `ftps` | 21 | TLS before login through AUTH TLS |
| Implicit FTPS | `ftps-implicit` | 990 | TLS before FTP greeting |
| FTP | `ftp` | 21 | Plaintext; explicit opt-in required |

## Verified FTPS

FTPS uses platform CA trust and hostname verification by default. A private CA bundle can be supplied with `--ca-file`. Both Explicit and Implicit FTPS force private data-channel protection with `PROT P`.

```powershell
$env:CLI_TOOL_FTP_PASSWORD="your-password"
device-cli-transfer list `
  --protocol ftps `
  --host ftp.example.test `
  --username readonly `
  --ca-file configs\lab-ca.pem `
  --remote-root /exports `
  --remote-path .
```

Implicit FTPS:

```powershell
device-cli-transfer stat `
  --protocol ftps-implicit `
  --host ftp.example.test `
  --username readonly `
  --remote-root /exports `
  --remote-path inventory.json
```

`--allow-unverified-tls` is an explicit troubleshooting escape hatch. It disables certificate and hostname verification, is recorded in the report, and must not be used in CI.

## Plain FTP

Plain FTP sends credentials, commands, metadata, and file contents without encryption. Every invocation requires `--allow-insecure-ftp`.

```powershell
device-cli-transfer exists `
  --protocol ftp `
  --allow-insecure-ftp `
  --host 192.0.2.10 `
  --username readonly `
  --remote-root /exports `
  --remote-path inventory.json
```

## Legacy UNIX listing

MLSD and MLST remain the default. A server that rejects those commands can use the strict UNIX parser only when both options are present:

```powershell
device-cli-transfer list `
  --protocol ftp `
  --allow-insecure-ftp `
  --allow-legacy-listing `
  --legacy-list-format unix `
  --host 192.0.2.10 `
  --username readonly `
  --remote-root /exports
```

The fallback runs only for unsupported-command replies. Authentication, permission, path, malformed metadata, special-file, and unsafe-name errors remain failures. Every LIST line must match the selected format; unknown lines are never skipped. Symlinks are identified and remain ineligible for download. UNIX LIST timestamps are validated but directory-list results keep `modified_time` unknown because the server timezone and missing year cannot be inferred safely. Reports record whether legacy fallback was enabled and which method actually handled a listing.

FTP and FTPS use passive mode by default. `--active-mode` is available only when the lab network requires it.

## Fail-closed metadata

Structured directory listing prefers MLSD, retrying without OPTS facts when necessary. File metadata prefers MLST. SIZE alone is never used to infer that an entry is a regular non-symlink file. When strict legacy UNIX mode is explicitly enabled, LIST supplies type and size while MDTM may supply an exact file timestamp. Without MLSD/MLST or an explicit strict legacy format, the operation fails closed.

All protocols retain:

- bounded absolute remote root;
- traversal and invalid-path rejection;
- regular non-symlink download policy;
- maximum download size;
- temporary partial file cleanup;
- byte-count and remote metadata drift validation;
- SHA-256 result;
- atomic local commit;
- no upload, delete, rename, chmod, mkdir, or recursive download.
