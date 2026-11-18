"""Local/file storage helpers for prep and TTS workers."""

from __future__ import annotations

import os
import pathlib
import shutil

import httpx


def _local_path(url: str) -> pathlib.Path:
    if url.startswith("file://"):
        return pathlib.Path(url[len("file://"):])
    if url.startswith("s3://"):
        raise ValueError(
            f"s3:// storage is not supported in this deployment; use file:// or an absolute path (got {url})"
        )
    return pathlib.Path(url)


def download_bytes(url: str, http_client: httpx.Client | None = None) -> bytes:
    """Download a file from file://, an absolute path, or http(s)://."""
    if url.startswith("file://"):
        return _local_path(url).read_bytes()
    if url.startswith("s3://"):
        raise ValueError(
            f"s3:// storage is not supported in this deployment; use file:// or an absolute path (got {url})"
        )
    if url.startswith("http://") or url.startswith("https://"):
        client = http_client or httpx.Client(timeout=30)
        response = client.get(url)
        response.raise_for_status()
        return response.content
    if os.path.isabs(url):
        return pathlib.Path(url).read_bytes()
    raise ValueError(f"Unsupported URL scheme or path: {url}")


def upload_package_dir(output_prefix: str, files: dict[str, bytes | str]) -> str:
    """Write package files under a local directory. Returns path ending with '/'."""
    dest = _local_path(output_prefix)
    dest.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        if isinstance(data, str):
            (dest / name).write_text(data)
        else:
            (dest / name).write_bytes(data)
    return str(dest) + "/"


def download_dir(url: str, dest_dir: str | pathlib.Path) -> None:
    """Copy all files from a local package directory into dest_dir."""
    src = _local_path(url)
    dest = pathlib.Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.is_file():
            shutil.copy2(item, dest / item.name)


def upload_dir(src_dir: str | pathlib.Path, output_prefix: str) -> str:
    """Copy a directory's contents to output_prefix. Returns path ending with '/'."""
    src = pathlib.Path(src_dir)
    dest = _local_path(output_prefix)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return str(dest) + "/"
