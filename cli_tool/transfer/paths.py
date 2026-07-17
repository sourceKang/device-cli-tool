from __future__ import annotations

from pathlib import PurePosixPath


class UnsafeRemotePathError(ValueError):
    """Raised when a remote path escapes the configured transfer root."""


def resolve_remote_path(remote_root: str, remote_path: str) -> str:
    if not isinstance(remote_root, str) or not remote_root.strip():
        raise UnsafeRemotePathError("remote_root must be a non-empty absolute POSIX path")
    if not isinstance(remote_path, str) or not remote_path.strip():
        raise UnsafeRemotePathError("remote_path must be a non-empty POSIX path")
    if "\x00" in remote_root or "\x00" in remote_path:
        raise UnsafeRemotePathError("remote path cannot contain NUL")
    if "\\" in remote_root or "\\" in remote_path:
        raise UnsafeRemotePathError("remote path must use POSIX separators")

    root = PurePosixPath(remote_root)
    requested = PurePosixPath(remote_path)
    if not root.is_absolute():
        raise UnsafeRemotePathError("remote_root must be an absolute POSIX path")
    if ".." in root.parts or ".." in requested.parts:
        raise UnsafeRemotePathError("remote path traversal is not allowed")

    candidate = requested if requested.is_absolute() else root / requested
    if candidate.parts[: len(root.parts)] != root.parts:
        raise UnsafeRemotePathError(f"remote path is outside configured root: {remote_root}")
    return str(candidate)
