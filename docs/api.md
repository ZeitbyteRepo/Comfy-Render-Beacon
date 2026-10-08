# Render Beacon Device API

Base URL: `http://<bridge-lan-address>:8220`

The API is observational only. It exposes no workload mutation routes.

## Endpoints

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/v1/health` | Bridge and ComfyUI reachability |
| `GET` | `/v1/state` | Normalized render pipeline and telemetry |
| `GET` | `/v1/preview.jpg` | Latest bounded JPEG preview, when available |
| `GET` | `/v2/state` | V2 rail, pipeline, telemetry, and completed-media descriptor |
| `GET` | `/v2/media/{id}/frame/{n}.jpg` | Bounded completed-media JPEG derivative |
| `POST` | `/v1/state` | Not allowed; must return `405` |

## State contract

`GET /v1/state` returns:

```json
{
  "schema_version": 1,
  "read_only": true,
  "mode": "idle | running | completed | error | offline",
  "clock": "HH:MM",
  "updated_at_ms": 0,
  "active": null,
  "queue": {
    "running": 0,
    "pending": 0
  },
  "gpu": {
    "utilization_percent": 0,
    "memory_used_mib": 0,
    "memory_total_mib": 0,
    "temperature_c": 0
  },
  "system": {
    "cpu_percent": 0,
    "ram_used_bytes": 0,
    "ram_total_bytes": 0
  }
}
```

While a render or retained terminal state exists, `active.pipeline` is the stable firmware-facing object:

```json
{
  "profile": "h3 | t2i",
  "active_stage": 0,
  "master_percent": 0,
  "step": {"value": 0, "max": 0},
  "stages": [
    {"name": "Prepare", "state": "active", "percent": 0},
    {"name": "Generate", "state": "upcoming", "percent": 0},
    {"name": "Finish", "state": "upcoming", "percent": 0}
  ],
  "metadata": {
    "model": null,
    "width": null,
    "height": null,
    "duration_seconds": null,
    "fps": null,
    "steps": null,
    "elapsed_ms": 0
  }
}
```

Firmware must consume this normalized object and must not interpret raw ComfyUI node class names.

## V2 display contract

V1 remains available. V2 is an additive, read-only device DTO. Its `rail` object has exactly
three fields, in display order: `queue`, `model_name`, and `modality_icon`. `model_name` is
selected from the bridge registry (unknown checkpoints become `Unknown model`); raw checkpoint
filenames are never copied into V2. `modality_icon` is the closed enum
`image | video | audio | unknown`; there is no modality text-label field.

`completed_media` is either `null` or a descriptor for GET-only JPEG frames. Stills are exact
480×320 baseline JPEGs with a 10,000 ms hold. Videos are bounded JPEG sequences whose
`loop_count` is exactly `3`. Audio is represented only by a 480×320 waveform card and exact
`duration_ms`; no audio-content endpoint exists. The cache installs a complete derivative
atomically and is bounded to four items, 4 MiB total, 24 video frames, and 65,536 bytes per
frame. Missing, malformed, oversized, or unknown media fails closed with `404` or no descriptor.

Each terminal output advances `completion_sequence` once. `completion_status` is `ready` only
after a complete derivative is installed; conversion failures publish `failed` with
`completed_media: null`, so a prior completion cannot be replayed as the new result.

## Progress semantics

| Profile | Stage 1 | Stage 2 | Stage 3 | Weights |
|---|---|---|---|---|
| H3 | Prepare | Generate | Finish | `10 / 80 / 10` |
| T2I | Generate | Assemble | Save | `80 / 10 / 10` |

`master_percent` is pipeline completion, not ETA or elapsed time. The bridge clamps it to `0..100` and does not allow it to regress below `prior_master_percent`.

## Refresh behavior

- Bridge observer refresh: `500 ms`.
- Firmware state poll: `500 ms`.
- Preview poll while running: `1500 ms`.
- CPU percentage is calculated from consecutive `/proc/stat` samples; the first sample may report zero.
- RAM byte fields are 64-bit values.
