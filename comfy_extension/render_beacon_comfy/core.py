from __future__ import annotations

import io
import ipaddress
from typing import Any

from PIL import Image, ImageOps


def is_loopback_peer(peer: str | None) -> bool:
    if not peer:
        return False
    try:
        address = ipaddress.ip_address(peer)
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.is_loopback
    except ValueError:
        return False


def _state_name(state: Any) -> str:
    value = getattr(state, "value", state)
    return str(value)


def serialize_progress(registry: Any) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    for node_id, state in list(registry.nodes.items()):
        value = state["value"]
        maximum = state["max"]
        percent = int(value * 100 / maximum) if maximum else None
        try:
            display_node_id = registry.dynprompt.get_display_node_id(node_id)
        except Exception:
            display_node_id = node_id
        try:
            class_type = registry.dynprompt.get_node(node_id).get("class_type")
        except Exception:
            class_type = None
        nodes.append(
            {
                "node_id": node_id,
                "display_node_id": display_node_id,
                "class_type": class_type,
                "state": _state_name(state["state"]),
                "value": value,
                "max": maximum,
                "percent": percent,
                "scope": "node",
            }
        )
    return {"prompt_id": registry.prompt_id, "nodes": nodes}


def encode_preview(image: Image.Image) -> bytes:
    contained = ImageOps.contain(image.convert("RGB"), (280, 176))
    canvas = Image.new("RGB", (280, 176), (12, 15, 20))
    canvas.paste(contained, ((280 - contained.width) // 2, (176 - contained.height) // 2))
    payload = io.BytesIO()
    canvas.save(payload, format="JPEG", quality=72, optimize=True)
    return payload.getvalue()
