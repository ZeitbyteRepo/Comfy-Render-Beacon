# Render Beacon operations

## Runtime layout

Choose local paths and addresses appropriate to your host. The examples below assume:

| Component | Example |
|---|---|
| Repository | `~/Comfy-Render-Beacon` |
| Bridge service | user-level `render-beacon.service` |
| Bridge endpoint | `http://<bridge-lan-address>:8220` |
| ComfyUI endpoint | `http://127.0.0.1:8188` |
| Companion source | `comfy_extension/render_beacon_comfy` |
| Firmware build | `firmware/.pio/build/hosyond-esp32-32e-st7796/firmware.bin` |

Do not expose the bridge directly to the public Internet. It is designed for a trusted LAN and intentionally has no application-authentication layer.

## Bridge operations

```bash
systemctl --user status render-beacon.service
journalctl --user -u render-beacon.service -n 100 --no-pager
curl -fsS http://<bridge-lan-address>:8220/v1/health
curl -fsS http://<bridge-lan-address>:8220/v1/state
```

A write-route regression check must return `405`:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  http://<bridge-lan-address>:8220/v1/state
```

## Companion-extension promotion gate

Do not install, remove, or restart the companion while either ComfyUI queue count is nonzero:

```bash
python3 - <<'PY'
import json, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8188/queue') as response:
    queue = json.load(response)
print(len(queue.get('queue_running', [])), len(queue.get('queue_pending', [])))
PY
```

After both counts are zero:

1. Copy or link `comfy_extension/render_beacon_comfy` into the intended ComfyUI `custom_nodes` directory.
2. Restart ComfyUI using the deployment's normal managed procedure.
3. Require loopback `GET /render-beacon/v1/progress` to return `200`.
4. Require extension POST attempts to return `405`.
5. Observe a real render and verify node-local progress and a decodable bounded preview.
6. Reverify the existing ComfyUI queue, history, and system routes.

### Companion rollback

If import or route qualification fails:

1. Wait until the ComfyUI queue is empty.
2. Stop ComfyUI through its normal managed procedure.
3. Remove only the expected Render Beacon companion installation.
4. Restart ComfyUI.
5. Verify `/system_stats`, `/queue`, and `/history` still work.
6. Verify the bridge degrades safely to queue/history/GPU telemetry without live sampler preview.

## Firmware deployment gate

Before flashing:

- identify the exact serial device through read-only USB/udev inspection;
- confirm no process owns it;
- obtain explicit authorization to open/reset/flash it;
- build and retain the current application firmware;
- capture a full rollback image;
- store rollback images outside Git because they may contain NVS credentials;
- never combine `pio run -t upload` with a build-only check.

Example rollback capture:

```bash
python3 ~/.platformio/packages/tool-esptoolpy/esptool.py \
  --chip esp32 \
  --port /dev/serial/by-id/<your-esp32-adapter> \
  --baud 460800 read_flash 0x0 0x400000 /secure/path/esp32-full-flash.bin
sha256sum /secure/path/esp32-full-flash.bin
```

After an approved flash, use the NVS provisioning helper in `firmware/README.md`. Do not compile Wi-Fi credentials into firmware.

## Verification

```bash
uv run pytest -q

cd firmware
pio run
```

Then verify:

- `GET /v1/health` reports `read_only: true` and ComfyUI reachability;
- `GET /v1/state` conforms to `docs/api.md`;
- all unsupported POST requests return `405`;
- the ESP32 repeatedly performs only bounded GET requests;
- disconnecting ComfyUI or the bridge produces a safe degraded/offline display;
- no service restart count increased unexpectedly.

## ESP32 JSON-stack pitfall

Do not place a 16 KiB `StaticJsonDocument` inside `updateState()`. Arduino reserves the entire local frame on function entry; this can overflow the loop-task stack before the HTTP request leaves the board and produce a repeating watchdog reset. A global 16 KiB static document can also exceed this target's linked DRAM budget. Use a function-local `DynamicJsonDocument` so its payload is heap-backed and released after the state update.
