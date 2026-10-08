# Render Beacon UI Specification

## Display

- Native resolution: `480×320` landscape.
- Read-only physical ComfyUI monitor.
- Top rail: local bridge clock on the left; render elapsed time on the right.
- No Render Beacon branding or decorative machine title.

## Main stage

The media-aware layout keeps the exact left rail and uses a `352×203` process surface at
screen position `(110,45)`.

| Element | Screen coordinates | Size |
|---|---:|---:|
| Master radial | process-local `(57,8)` | `116×116` |
| Three stage rows | process-local `(10,130)` | `210×20` each, 2 px gaps |
| Recent-media column | screen `(340,45)` | three `112×64` slots, 4 px gaps |

Geometry invariants:

- Exactly one radial is rendered.
- Master stroke: `5 px`.
- Stage numbers are plain `1 / 2 / 3`.
- Master caption is `Master flow` and remains mechanically centered under the percentage.
- Stage rows show name, state, and bounded percent/Done text without scan animation.
- The right column shows the newest three cached outputs as fixed 112×64 thumbnails.

## Stage states

- Completed: muted desaturated green row accent and completion text.
- Active: orange row accent.
- Upcoming: outlined/dim row treatment.
- Master radial remains the strongest orange instrument.

## Footer telemetry

Five equal-width vertical instruments with `80×4` bars:

1. GPU utilization
2. CPU utilization
3. VRAM used
4. RAM used
5. GPU temperature

Columns use 80 px widths and 11 px gaps across the 444 px surface.

## Thumbnail and takeover behavior

- Fetch thumbnails only when the newest-three ID list changes, after LVGL flushes slot backgrounds.
- Retry a failed thumbnail fetch no more often than once every two seconds.
- Reuse the single bounded JPEG buffer; do not retain three compressed or decoded thumbnail buffers.
- Fullscreen still/video/audio takeover remains unchanged and preempts thumbnail drawing.
- Mark thumbnails dirty after takeover so the process screen redraws them exactly once.

## Typography and LVGL requirements

- Instrument Sans 12 and 29 are uncompressed LVGL fonts.
- Generated font assets must use `--no-compress` and `.bitmap_format = 0`.
- `LV_USE_ARC = 1`.
- Build flag: `LV_LVGL_H_INCLUDE_SIMPLE=1`.
- Build flag: `ARDUINOJSON_USE_LONG_LONG=1` for 64-bit RAM byte fields.

## Runtime safety

- Build the object tree once; update values, text, and styles in place.
- Clamp all arc/bar values to `0..100`.
- Clamp `active_stage` to `0..2`.
- Reset the rack explicitly when `active.pipeline` is absent.
- Never allocate the 16 KiB JSON document as `StaticJsonDocument` on the loop-task stack. Use heap-backed `DynamicJsonDocument` so state polling cannot overflow the ESP32 loop stack.
