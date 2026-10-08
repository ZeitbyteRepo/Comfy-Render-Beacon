# Render Beacon firmware

Target: Hosyond/LCDWIKI ESP32-32E with an ST7796 480×320 display.

## Safety

- Firmware is read-only toward Render Beacon and ComfyUI.
- No Wi-Fi credentials are compiled into the image.
- SSID, password, and bridge URL are stored in ESP32 NVS namespace `renderbeacon`.
- Provisioning requires the explicit `--confirm-reset` flag because opening serial may pulse DTR/RTS and reset the board.
- Production firmware does not require or access an SD card. The separately selected
  `sd-readonly-probe` diagnostic is the only SD-aware target.
- A successful build is not authorization to open a serial port or flash hardware.

## NVS keys

| Key | Meaning | Source default |
|---|---|---|
| `wifi_ssid` | Wi-Fi network name | empty |
| `wifi_password` | Wi-Fi password | empty |
| `bridge_url` | LAN-reachable bridge base URL | example private-LAN address |

The example bridge URL in source is not a discovery mechanism. Set the actual LAN-reachable URL during provisioning.

## Build

```bash
cd firmware
pio run -e hosyond-esp32-32e-st7796
```

Qualified source baseline:

- RAM: 113,560 bytes of 327,680 bytes (34.7%)
- application flash: 1,196,157 bytes of 1,310,720 bytes (91.3%)

Flashing is deliberately a separate operation. Preserve a rollback image before any approved flash, but never commit that image: a full ESP32 flash backup may include Wi-Fi credentials in NVS.

## Separate read-only SD registration diagnostic

The one-shot `sd-readonly-probe` environment excludes `main.cpp`, does not use serial,
and renders bounded mount/card/capacity/root/optional-fixture results directly on the TFT.
Build it without uploading:

```bash
cd firmware
pio run -e sd-readonly-probe
```

See [`SD_READONLY_PROBE.md`](SD_READONLY_PROBE.md) for its enforced read-only contract,
the unresolved `TFT_RST=12` versus vendor `TFT_RST=-1` concern, physical verification,
and rollback procedure. Building this target is not authorization to flash it.

## Provision after an approved flash

The helper prompts without echoing the password, sends hex-encoded fields, writes NVS, and requires the board to reply `RB1 OK` before reporting success.

```bash
cd Comfy-Render-Beacon
uv run --with pyserial scripts/provision.py \
  --port /dev/serial/by-id/<your-esp32-adapter> \
  --bridge-url http://<bridge-lan-address>:8220 \
  --confirm-reset
```

Do not run this merely to identify hardware: opening common CH340 serial interfaces can reset the ESP32.
