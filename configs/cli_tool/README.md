# CLI Tool Catalogs

This folder contains read-only command catalogs for the reusable `cli_tool` package.

Current scope:

- `generic.yaml`: cross-family minimal smoke catalog with `show_version`.
- `neox.yaml`: NeoX-specific read-only catalog; currently mirrors `show_version` until more NeoX-only commands are confirmed.
- `ies52xx.yaml`: IES52XX-specific read-only catalog; currently only `show_version`.
- `olt140x.yaml`: OLT140X-specific read-only catalog; currently only `show_version`.

IES52XX and OLT140X catalogs should receive additional commands only after their command syntax, prompt behavior, and safe read-only commands are confirmed.

Catalog rules:

- Do not store device IPs, usernames, passwords, tokens, or lab-specific secrets here.
- Mark read-only commands with `readonly: true`.
- Do not add mutating config commands until the protected configure workflow and allowlist policy are in place.
- Prefer command ids that describe intent, such as `show_version`, instead of device-specific spelling.
