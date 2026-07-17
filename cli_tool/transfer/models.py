from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RemoteFileInfo:
    path: str
    size: int
    modified_time: float | None
    is_directory: bool
    is_regular_file: bool
    is_symlink: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DownloadResult:
    remote_path: str
    local_path: str
    size: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)
