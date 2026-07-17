from __future__ import annotations

import hashlib
from typing import Iterable

from cli_tool.transfer.models import DownloadResult, RemoteFileInfo


def identifier_reference(value: str, *, include_value: bool = False) -> str:
    if include_value:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}"


def file_info_payload(
    info: RemoteFileInfo,
    *,
    include_paths: bool = False,
) -> dict[str, object]:
    payload = info.as_dict()
    payload["path"] = identifier_reference(info.path, include_value=include_paths)
    return payload


def file_list_payload(
    items: Iterable[RemoteFileInfo],
    *,
    include_paths: bool = False,
) -> list[dict[str, object]]:
    return [file_info_payload(item, include_paths=include_paths) for item in items]


def download_payload(
    result: DownloadResult,
    *,
    include_paths: bool = False,
) -> dict[str, object]:
    payload = result.as_dict()
    payload["remote_path"] = identifier_reference(result.remote_path, include_value=include_paths)
    payload["local_path"] = identifier_reference(result.local_path, include_value=include_paths)
    return payload
