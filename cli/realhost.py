from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


class _Units:
    def __init__(self, directory: str):
        self.directory = Path(directory)

    def __contains__(self, name: str) -> bool:
        return (self.directory / name).exists()


class _Tasks:
    def __init__(self):
        self._known: dict[str, str] = {}

    def __contains__(self, name: str) -> bool:
        if name in self._known:
            return True
        if sys.platform.startswith("win"):
            result = subprocess.run(
                ["schtasks", "/Query", "/TN", name],
                capture_output=True,
                check=False,
            )
            return result.returncode == 0
        return False

    def get(self, name: str, default=None):
        return self._known.get(name, default)

    def remember(self, name: str, command: str) -> None:
        self._known[name] = command

    def forget(self, name: str) -> None:
        self._known.pop(name, None)


class RealHost:
    """Linux systemd host, or a Windows scheduled-task host. Tests use MemoryHost."""

    def __init__(self):
        self.os_name = "windows" if sys.platform.startswith("win") else "linux"
        self.distro = _distro()
        self.restarts: list[str] = []
        self.packages: list[str] = []
        self.units = _Units("/etc/systemd/system")
        self.tasks = _Tasks()
        self.reboot_pending = _reboot_pending()
        self.docker_ready = _docker_ready()

    def is_root(self) -> bool:
        if self.os_name == "windows":
            try:
                import ctypes

                return bool(ctypes.windll.shell32.IsUserAnAdmin())
            except Exception:
                return False
        return os.geteuid() == 0

    def nvidia_smi(self) -> bool:
        binary = shutil.which("nvidia-smi")
        if not binary:
            return False
        result = subprocess.run([binary, "-L"], capture_output=True, check=False)
        return result.returncode == 0

    def local_ips(self) -> list[str]:
        return ["127.0.0.1"]

    def read(self, path: str) -> str | None:
        file = Path(path)
        if not file.exists():
            return None
        return file.read_text()

    def write(self, path: str, content: str) -> None:
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
        if file.name.endswith(".env"):
            os.chmod(file, 0o600)

    def write_bytes(self, path: str, data: bytes) -> None:
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)

    def chmod(self, path: str, mode: int) -> None:
        os.chmod(path, mode)

    def remove(self, path: str) -> None:
        file = Path(path)
        if file.is_file():
            file.unlink()

    def remove_prefix(self, prefix: str) -> None:
        path = Path(prefix)
        if path.exists():
            import shutil as sh

            sh.rmtree(path)

    def install_packages(self, names: list[str]) -> None:
        if not names:
            return
        self.packages.extend(names)
        if self.os_name == "windows":
            for name in names:
                subprocess.run(
                    [
                        "winget",
                        "install",
                        "--exact",
                        "--id",
                        name,
                        "--accept-package-agreements",
                        "--accept-source-agreements",
                    ],
                    check=True,
                )
        else:
            subprocess.run(["apt-get", "install", "-y", *names], check=True)
        self.reboot_pending = _reboot_pending()
        self.docker_ready = _docker_ready()

    def install_unit(self, name: str, text: str) -> None:
        path = Path("/etc/systemd/system") / name
        path.write_text(text)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "--now", name], check=True)

    def restart(self, name: str) -> None:
        self.restarts.append(name)
        if self.os_name == "windows":
            subprocess.run(["schtasks", "/End", "/TN", name], check=False)
            subprocess.run(["schtasks", "/Run", "/TN", name], check=True)
            return
        subprocess.run(["systemctl", "restart", name], check=True)

    def stop_unit(self, name: str) -> None:
        subprocess.run(["systemctl", "disable", "--now", name], check=False)
        path = Path("/etc/systemd/system") / name
        if path.exists():
            path.unlink()
        subprocess.run(["systemctl", "daemon-reload"], check=False)

    def install_task(self, name: str, command: str) -> None:
        subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"], check=False)
        subprocess.run(
            [
                "schtasks",
                "/Create",
                "/TN",
                name,
                "/SC",
                "ONSTART",
                "/RU",
                "SYSTEM",
                "/RL",
                "HIGHEST",
                "/TR",
                command,
                "/F",
            ],
            check=True,
        )
        self.tasks.remember(name, command)

    def stop_task(self, name: str) -> None:
        subprocess.run(["schtasks", "/Delete", "/TN", name, "/F"], check=False)
        self.tasks.forget(name)


def detect_host() -> RealHost:
    return RealHost()


def _distro() -> str:
    text = ""
    path = Path("/etc/os-release")
    if path.exists():
        text = path.read_text().lower()
    if "ubuntu" in text:
        return "ubuntu"
    if "debian" in text:
        return "debian"
    return platform.system().lower()


def _reboot_pending() -> bool:
    if sys.platform.startswith("win"):
        result = subprocess.run(
            ["reg", "query", r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager", "/v", "PendingFileRenameOperations"],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    return Path("/var/run/reboot-required").exists()


def _docker_ready() -> bool:
    if not shutil.which("docker"):
        return False
    result = subprocess.run(["docker", "info"], capture_output=True, check=False)
    return result.returncode == 0
