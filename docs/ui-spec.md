# Render Beacon UI Specification

## Display

- Native resolution: `480×320` landscape.
- Read-only physical ComfyUI monitor.
- Top rail: local bridge clock on the left; render elapsed time on the right.
- No Render Beacon branding or decorative machine title.

## Main stage

One continuous `444×162` surface at screen position `(18,45)`.

| Element | Screen coordinates | Size |
|---|---:|---:|
| Stage rack | `(47,54)` | `246×128` |
| Three stage bays | rack-local `0,84,168` | `78×128` each |
| Stage radial | bay-local `(9,32)` | `60×60` |
| Master radial | `(317,58)` | `116×116` |
| Metadata strip | `(18,219)` | `444×29` |

Geometry invariants:

- Rack-to-master gap: `24 px`.
- Surface-relative outer margins: `29 px` left and right.
- Master stroke: `4 px`.
- Stage numbers are plain `1 / 2 / 3`.
- Master caption is `Master flow` and remains mechanically centered under the percentage.

## Stage states

- Completed: muted desaturated green annulus and completion text.
- Active: orange annulus, orange top rail, and bounded scan line.
- Upcoming: outlined/dim treatment.
- Master radial remains the strongest orange instrument.

The active scan line uses:

- Start y: `23` within the bay.
- Travel: `68 px`.
- Range: `23..91`.
- Period: `1400 ms` triangle wave.
- Width: `60 px`, matching the radial.

It must never pass through the stage name or status text.

## Footer telemetry

Five equal-width vertical instruments with `80×4` bars:

1. GPU utilization
2. CPU utilization
3. VRAM used
4. RAM used
5. GPU temperature

Columns use 80 px widths and 11 px gaps across the 444 px surface.

## Preview behavior

- Process-rack replacement panel: `(47,54)`, `246×128`.
- Bridge preview maximum: `280×176`.
- TJpg scale 2 output: up to `140×88`.
- Centered preview image origin: approximately `(100,74)`.
- Hide only the three-stage rack and its detail caption.
- Keep the master radial, metadata strip, top rail, and footer telemetry visible.
- Force the LVGL hide/show refresh before direct TJpg drawing.
- Skip scan movement while the rack is hidden to avoid invalidating the direct-drawn preview.

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
