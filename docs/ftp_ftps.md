# FTPS and FTP read-only transfers

Version 0.5.0 extends the transfer contract to Explicit FTPS, Implicit FTPS, and opt-in plaintext FTP.

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

FTP and FTPS use passive mode by default. `--active-mode` is available only when the lab network requires it.

## Fail-closed metadata

Structured directory listing requires MLSD. File metadata uses MLST and may fall back to SIZE plus MDTM for regular files. The tool does not guess UNIX or DOS LIST formats because that could misclassify paths or silently accept malformed metadata.

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
