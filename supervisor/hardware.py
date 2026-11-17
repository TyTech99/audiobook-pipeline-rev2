from __future__ import annotations

import json
import os
from pathlib import Path


def detect_hardware() -> dict:
    recorded = os.environ.get("HARDWARE_FILE")
    if recorded:
        return json.loads(Path(recorded).read_text())
    cpu = 1
    ram_gb = 1.0
    try:
        import psutil

        cpu = psutil.cpu_count(logical=True) or 1
        ram_gb = round(psutil.virtual_memory().total / (1024**3), 1)
    except Exception:
        pass
    return {"cpu": cpu, "ram_gb": ram_gb, "gpu": _gpu()}


def _gpu():
    try:
        import pynvml

        pynvml.nvmlInit()
        try:
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(handle)
            if isinstance(name, bytes):
                name = name.decode()
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            vram_gb = round(memory.total / (1024**3), 1)
            return {"model": name, "vram_gb": vram_gb}
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        return None
