import json

from supervisor.hardware import detect_hardware


def test_hardware_file_skips_live_probes(tmp_path, monkeypatch):
    recorded = {"cpu": 4, "ram_gb": 16.0, "gpu": {"model": "Test GPU", "vram_gb": 8.0}}
    path = tmp_path / "hardware.json"
    path.write_text(json.dumps(recorded))
    monkeypatch.setenv("HARDWARE_FILE", str(path))
    assert detect_hardware() == recorded


def test_missing_hardware_file_uses_local_probes(monkeypatch):
    monkeypatch.delenv("HARDWARE_FILE", raising=False)
    info = detect_hardware()
    assert info["cpu"] >= 1
    assert info["ram_gb"] > 0
    assert "gpu" in info
