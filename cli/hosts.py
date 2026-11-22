from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field


@dataclass
class MemoryHost:
    """In-memory machine used by tests. Records packages, files, and services."""

    os_name: str = "linux"
    root: bool = True
    distro: str = "ubuntu"
    gpu: bool = True
    docker_ready: bool = True
    reboot_pending: bool = False
    ips: list[str] = field(default_factory=lambda: ["127.0.0.1"])
    files: dict[str, str | bytes] = field(default_factory=dict)
    packages: list[str] = field(default_factory=list)
    units: dict[str, str] = field(default_factory=dict)
    tasks: dict[str, str] = field(default_factory=dict)
    restarts: list[str] = field(default_factory=list)
    running: list[str] = field(default_factory=list)

    def is_root(self) -> bool:
        return self.root

    def nvidia_smi(self) -> bool:
        return self.gpu

    def local_ips(self) -> list[str]:
        return list(self.ips)

    def read(self, path: str) -> str | None:
        return self.files.get(path)

    def write(self, path: str, content: str) -> None:
        self.files[path] = content

    def write_bytes(self, path: str, data: bytes) -> None:
        self.files[path] = data

    def chmod(self, path: str, mode: int) -> None:
        pass  # no-op in memory host

    def remove(self, path: str) -> None:
        self.files.pop(path, None)

    def remove_prefix(self, prefix: str) -> None:
        for path in list(self.files):
            if path.startswith(prefix):
                self.files.pop(path)

    def install_packages(self, names: list[str]) -> None:
        self.packages.extend(names)

    def install_unit(self, name: str, text: str) -> None:
        self.units[name] = text
        if name not in self.running:
            self.running.append(name)

    def restart(self, name: str) -> None:
        self.restarts.append(name)

    def stop_unit(self, name: str) -> None:
        self.units.pop(name, None)
        self.running = [item for item in self.running if item != name]

    def install_task(self, name: str, command: str) -> None:
        self.tasks[name] = command

    def stop_task(self, name: str) -> None:
        self.tasks.pop(name, None)


class CommandRunner:
    def __init__(self, run=None):
        self.run = run or _subprocess_run
        self.os_name = "windows" if sys.platform.startswith("win") else "linux"

    def install_packages(self, names: list[str]) -> None:
        if not names:
            return
        if self.os_name == "windows":
            for name in names:
                self.run(
                    [
                        "winget",
                        "install",
                        "--exact",
                        "--id",
                        name,
                        "--accept-package-agreements",
                        "--accept-source-agreements",
                    ]
                )
        else:
            self.run(["apt-get", "install", "-y", *names])


def _subprocess_run(argv: list[str]) -> None:
    import subprocess

    subprocess.run(argv, check=True)


def linux_paths():
    return {
        "coordinator_env": "/etc/audiobook/coordinator.env",
        "node_env": "/etc/audiobook/node.env",
        "coordinator_prefix": "/opt/audiobook/coordinator",
        "node_prefix": "/opt/audiobook/supervisor",
        "coordinator_unit": "audiobook-coordinator.service",
        "node_unit": "audiobook-supervisor.service",
    }


def windows_paths():
    return {
        "node_env": r"C:\ProgramData\audiobook\node.env",
        "node_prefix": r"C:\Program Files\audiobook-supervisor",
        "node_task": "AudiobookSupervisor",
    }


def is_root() -> bool:
    if sys.platform.startswith("win"):
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0
