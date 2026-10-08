# Comfy Render Beacon

A physical, read-only ComfyUI telemetry and preview display for an ESP32 with a 480×320 ST7796 panel.

Render Beacon turns ComfyUI queue, progress, preview, GPU, and host telemetry into a small desk instrument. It deliberately cannot submit, interrupt, reorder, delete, or otherwise mutate workloads.

## Hardware target

The qualified target is a Hosyond/LCDWIKI ESP32-32E board with:

- classic ESP32, 4 MB flash;
- ST7796 480×320 display;
- no PSRAM requirement;
- no SD-card requirement.

The current firmware uses approximately 34.7% of RAM and 91.3% of the configured application partition. Treat flash headroom as constrained.

## Architecture

```text
ComfyUI
  ├─ existing read-only APIs
  └─ optional loopback-only companion GET routes
          │
          ▼
Render Beacon bridge
  ├─ normalizes queue, history, progress, system, and GPU state
  ├─ strips prompt text and full workflow graphs
  ├─ maps checkpoints through a fail-closed display-name registry
  └─ creates bounded completed-media JPEG derivatives
          │
          ▼
ESP32 over the local network
  ├─ GET-only polling
  ├─ LVGL status UI
  └─ streamed JPEG blocks without a second framebuffer
```

## Read-only contract

The bridge exposes only:

- `GET /v1/health`
- `GET /v1/state`
- `GET /v1/queue`
- `GET /v1/history?limit=N`
- `GET /v1/preview.jpg`
- `GET /v2/state`
- `GET /v2/media/{id}/frame/{n}.jpg`

It has no queue, interrupt, delete, upload, prompt-submission, workflow-mutation, or GPU-control route. OpenAPI and interactive documentation are disabled. Full prompt text and full workflow graphs are not returned.

Completed video/audio conversion requires host `ffmpeg` and `ffprobe`. The ESP32 receives only
baseline JPEGs: it never decodes MP4 or audio. See [`docs/api.md`](docs/api.md) for bounds and
takeover timing.

The bridge resolves completed sources beneath `RENDER_BEACON_OUTPUT_ROOT` (default
`/srv/ai/data/comfyui/output`) and requests only ComfyUI `type=output`; temp/input paths,
absolute paths, traversal, and output-root escapes are rejected.

## Bridge development

Requirements:

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- a reachable local ComfyUI instance

```bash
git clone https://github.com/ZeitbyteRepo/Comfy-Render-Beacon.git
cd Comfy-Render-Beacon
uv sync

export RENDER_BEACON_COMFY_URL=http://127.0.0.1:8188
# Optional deployment-specific GPU manager:
# export RENDER_BEACON_GPU_MANAGER_PATH=/path/to/gpu-manager
uv run uvicorn render_beacon.service:app \
  --app-dir bridge \
  --host 0.0.0.0 \
  --port 8220
```

Verify the read-only bridge:

```bash
curl -fsS http://127.0.0.1:8220/v1/health
curl -fsS http://127.0.0.1:8220/v1/state
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  http://127.0.0.1:8220/v1/state
# Expected: 405
```

Keep the bridge on a trusted LAN; it intentionally has no write routes or application authentication layer.

## ComfyUI companion extension

`comfy_extension/render_beacon_comfy` adds bounded, loopback-only progress and preview GET routes. It defines no execution nodes and no state-changing routes.

Install or link it into `ComfyUI/custom_nodes/render_beacon_comfy`, then restart ComfyUI only when its queue is empty. See [`docs/OPERATIONS.md`](docs/OPERATIONS.md) for the promotion and rollback gates.

## Firmware

Requirements:

- PlatformIO
- the exact supported board/display wiring, or an audited pin/configuration adaptation

```bash
cd firmware
pio run
```

A build never implies authorization to flash a connected board. Opening many ESP32 serial adapters toggles DTR/RTS and can reset the device. See [`firmware/README.md`](firmware/README.md) before provisioning or flashing.

Wi-Fi credentials are not compiled into the source or firmware image. They are entered at provisioning time and stored in the ESP32's NVS namespace.

## Tests

```bash
uv run pytest -q
# Current baseline: 39 passed

cd firmware
pio run
```

## Repository safety

Full flash backups are deliberately ignored because they can contain Wi-Fi credentials in NVS. Do not commit `.pio`, virtual environments, device backups, captured provisioning traffic, or local agent/editor configuration.

## Documentation

- [`PRODUCT.md`](PRODUCT.md) — product and visual principles
- [`docs/ui-spec.md`](docs/ui-spec.md) — exact 480×320 UI geometry and state behavior
- [`docs/api.md`](docs/api.md) — normalized device-facing API
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — deployment, verification, and rollback gates
- [`firmware/README.md`](firmware/README.md) — build and safe provisioning

Copyright © 2026 Zeitbyte. All rights reserved. Third-party code under `firmware/lib/` retains its own license files.
