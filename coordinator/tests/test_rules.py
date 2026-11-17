from coordinator.services.rules import effective_queues, valid_node_id


def test_effective_queues_drop_convert_without_gpu():
    assert effective_queues(["prep", "convert"], ["prep", "convert"], {"gpu": None}) == ["prep"]


def test_effective_queues_keep_convert_with_gpu():
    hardware = {"gpu": {"model": "RTX 4090", "vram_gb": 24}}
    assert effective_queues(["convert"], ["prep", "convert"], hardware) == ["convert"]


def test_node_id_slug():
    assert valid_node_id("gpu-study")
    assert not valid_node_id("GPU")
    assert not valid_node_id("has space")
    assert not valid_node_id("")
