import io
import sys
from pathlib import Path

import pytest
from PIL import Image

EXTENSION = Path(__file__).parents[1] / "comfy_extension"
sys.path.insert(0, str(EXTENSION))

from render_beacon_comfy.core import encode_preview, is_loopback_peer, serialize_progress


class State:
    def __init__(self, value, maximum, name):
        self.value = name
        self._data = {"value": value, "max": maximum, "state": self}

    def __getitem__(self, key):
        return self._data[key]


class DynamicPrompt:
    def get_display_node_id(self, node_id):
        return f"display-{node_id}"

    def get_node(self, node_id):
        return {"class_type": "KSampler"}


class Registry:
    prompt_id = "prompt-7"
    dynprompt = DynamicPrompt()
    nodes = {"17": State(6, 9, "running"), "18": State(0, 1, "pending")}


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_extension_accepts_only_loopback_peers(peer):
    assert is_loopback_peer(peer)


@pytest.mark.parametrize("peer", ["192.168.1.25", "10.0.0.3", "8.8.8.8", None, "garbage"])
def test_extension_rejects_non_loopback_peers(peer):
    assert not is_loopback_peer(peer)


def test_progress_serialization_is_compact_and_marks_scope():
    result = serialize_progress(Registry())
    assert result == {
        "prompt_id": "prompt-7",
        "nodes": [
            {
                "node_id": "17",
                "display_node_id": "display-17",
                "class_type": "KSampler",
                "state": "running",
                "value": 6,
                "max": 9,
                "percent": 66,
                "scope": "node",
            },
            {
                "node_id": "18",
                "display_node_id": "display-18",
                "class_type": "KSampler",
                "state": "pending",
                "value": 0,
                "max": 1,
                "percent": 0,
                "scope": "node",
            },
        ],
    }


def test_preview_encoding_is_fixed_device_jpeg():
    payload = encode_preview(Image.new("RGB", (1200, 800), "purple"))
    image = Image.open(io.BytesIO(payload))
    assert image.format == "JPEG"
    assert image.size == (280, 176)
    assert len(payload) < 65536
