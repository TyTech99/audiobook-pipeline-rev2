from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_commissioning_matches_the_container_install():
    commissioning = (ROOT / "docs/architecture/commissioning.md").read_text()
    adr = (ROOT / "docs/decisions/003-jit-workers.md").read_text()
    assert "Nothing in this document is implemented yet" not in commissioning
    assert "docker compose" in commissioning
    assert "/opt/audiobook/supervisor/venv" not in commissioning
    assert "Kubernetes" in adr
    assert "Rejected for simplicity" in adr
    assert "audiobook-supervisor" in adr
