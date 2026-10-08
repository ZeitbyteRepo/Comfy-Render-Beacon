# Product

<!-- impeccable:product-schema 1 -->

## Platform

embedded LVGL on ESP32, fixed 480×320 landscape TFT

## Users

Sam is the primary user. The device sits at arm’s length during local ComfyUI rendering and must communicate render state without requiring interaction with the workstation UI.

## Product Purpose

Render Beacon is a dedicated glanceable status instrument for local AI rendering. Success means the user can immediately understand whether ComfyUI is idle or rendering, how far the active node has progressed, and whether GPU memory or utilization is near its limit.

## Positioning

A read-only bridge combines ComfyUI queue/node telemetry with local NVIDIA GPU state and sends the normalized result to a physically separate ESP32 display. The device never submits, interrupts, or controls workflows.

## Operating Context

- Read primarily at arm’s length on a desk.
- Operates continuously while ComfyUI workflows render on the local RTX 3090.
- Uses Wi-Fi to poll a configured bridge URL on the trusted LAN.
- Rendering workflows may or may not emit preview frames.
- The physical layout must remain stable across idle, rendering, degraded, and offline states.

## Capabilities and Constraints

- ESP32-D0WD-V3 with 4 MiB flash.
- HOSYOND ESP32-32E ST7796 480×320 TFT.
- LVGL 8.3.11 with Arduino/PlatformIO.
- Read-only HTTP polling; no workflow submission, cancellation, or settings mutation.
- Displays model/workflow label, phase, node name/class, node-local progress, GPU utilization, VRAM utilization, temperature, queue state, connection state, and preview when genuinely available.
- A render-state placeholder may animate when no genuine preview is available, but it must not be presented as generated output.
- GPU and VRAM values require visually proportional indicators, not text alone.
- Node-local progress requires a radial indicator with numeric reinforcement.
- Geometry must not jump when state changes.

## Brand Commitments

- Product name: Render Beacon.
- The visual world should feel authored for Teenage Engineering hardware or Bungie’s Marathon game: disciplined instrument graphics, bold information hierarchy, hard-edged color fields, technical typography, and purposeful motion.
- Avoid generic gamer RGB, ornamental sci-fi chrome, excessive glow, fake glass, and dense unreadable telemetry.

## Evidence on Hand

- Production bridge and companion extension are running.
- Physical device is flashed, provisioned, and polling the bridge.
- Existing firmware source: `firmware/src/main.cpp`.
- Existing compiled and flashed target: `hosyond-esp32-32e-st7796`.
- Device rollback images are retained outside the repository because full flash captures may contain NVS credentials.

## Product Principles

1. State must be legible before detail.
2. Spatial stability is more valuable than squeezing in another metric.
3. Every graphic must encode real telemetry or clearly identify itself as ambient animation.
4. Read-only behavior is a product boundary, not an implementation detail.
5. The physical panel is the acceptance authority.
