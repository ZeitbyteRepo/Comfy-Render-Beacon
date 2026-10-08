#!/usr/bin/env python3
"""Provision Render Beacon NVS over USB serial.

Opening the serial port can reset the board through DTR/RTS. The command
therefore requires --confirm-reset and never prints the Wi-Fi password.
"""

from __future__ import annotations

import argparse
import getpass
import time


def _hex(value: str) -> str:
    return value.encode("utf-8").hex()


def encode_command(ssid: str, password: str, bridge_url: str) -> bytes:
    if not ssid or len(ssid.encode("utf-8")) > 32:
        raise ValueError("SSID must be 1-32 UTF-8 bytes")
    if len(password.encode("utf-8")) > 64:
        raise ValueError("Wi-Fi password must be at most 64 UTF-8 bytes")
    if not bridge_url.startswith("http://") or len(bridge_url.encode("utf-8")) > 128:
        raise ValueError("bridge URL must be an http:// URL of at most 128 bytes")
    return f"RB1\t{_hex(ssid)}\t{_hex(password)}\t{_hex(bridge_url)}\n".encode("ascii")


def decode_command(command: bytes) -> tuple[str, str, str]:
    parts = command.decode("ascii").rstrip("\n").split("\t")
    if len(parts) != 4 or parts[0] != "RB1":
        raise ValueError("invalid Render Beacon provision command")
    return tuple(bytes.fromhex(part).decode("utf-8") for part in parts[1:])  # type: ignore[return-value]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyUSB0")
    parser.add_argument(
        "--bridge-url",
        required=True,
        help="LAN-reachable bridge base URL, for example http://192.168.1.2:8220",
    )
    parser.add_argument(
        "--confirm-reset",
        action="store_true",
        help="acknowledge that opening serial may reset the ESP32",
    )
    args = parser.parse_args()
    if not args.confirm_reset:
        parser.error("refusing to open serial without --confirm-reset")

    ssid = input("Wi-Fi SSID: ")
    password = getpass.getpass("Wi-Fi password: ")
    command = encode_command(ssid, password, args.bridge_url)

    try:
        import serial
    except ImportError as exc:
        raise SystemExit("pyserial is required: uv run --with pyserial scripts/provision.py ...") from exc

    with serial.Serial(args.port, 115200, timeout=5) as device:
        time.sleep(1.5)
        device.reset_input_buffer()
        device.write(command)
        device.flush()
        response = device.readline().decode("utf-8", errors="replace").strip()
    if response != "RB1 OK":
        raise SystemExit(f"device did not confirm provisioning: {response or '<no response>'}")
    print("Render Beacon NVS provisioned; device is restarting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
