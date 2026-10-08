# Render Beacon read-only SD registration probe

This diagnostic is a separate, one-shot firmware image. It is not linked into the production Render Beacon UI and does not use Wi-Fi, NVS, or serial. Its only SD filesystem operations are a read-only root open and, when present, a bounded read of `/RB_SD_TEST.TXT`.

## Audited hardware contract

| Signal | GPIO | Diagnostic behavior |
|---|---:|---|
| MicroSD CS | 5 | Driven high before SPI initialization; passed explicitly to `SD.begin` |
| MicroSD SCK | 18 | Explicit HSPI clock |
| MicroSD MISO | 19 | Explicit HSPI input |
| MicroSD MOSI | 23 | Explicit HSPI output |
| External SPI CS | 21 | Driven high because the connector shares GPIO 18/19/23 |
| TFT/touch bus | 14/13/12 | Remains separate from the SD SPI pins |
| Card detect | none | Not inferred; socket pin 9 is NC |

Registration is deliberately fixed at 1 MHz:

```cpp
SD.begin(5, sdSpi, 1000000U, "/sd", 5, false)
```

The final `false` forbids format-on-mount. The probe application invokes no format, write, rename, create, or delete APIs. The linked Arduino SD/FS libraries may still contain dormant mutation-capable symbols; their presence does not mean the probe application calls them. Absence of the optional test file is reported as `NOT PRESENT (optional)` and does not fail registration. If the file exists but cannot be opened as a regular readable file, the probe fails.

## Mechanical qualification (no device access)

Run only a build; neither command opens serial or uploads:

```bash
uv run pytest -q tests/test_sd_readonly_probe_contract.py
cd firmware
pio run -e sd-readonly-probe
```

The static contract tests enforce target isolation, the exact pin/order/mount call, no direct application-source invocation of known mutation or serial APIs, the bounded optional read, truthful PASS/FAIL TFT fields, and retention of the documented reset-pin concern. PlatformIO compilation qualifies the exact ESP32/Arduino/TFT_eSPI dependency graph. Binary symbol inspection is expected to find dormant mutation-capable SD/FS library symbols and must not be used to claim those library paths are absent. Build output is under `firmware/.pio/build/sd-readonly-probe/`; `.pio` is ignored and must not be committed.

A passing build is not permission to connect, reset, open serial, or flash hardware.

## Approved physical verification procedure

Perform these steps only after explicit authorization to touch and flash the named Render Beacon:

1. Power the board down. Confirm the external SPI connector on CS21 has no active peripheral, and insert a known-good FAT-formatted MicroSD. No card-detect signal exists.
2. On a separate trusted reader, optionally place a short ASCII file named exactly `RB_SD_TEST.TXT` in the card root. Record the card/filesystem checksum or image hash before insertion if post-test non-mutation proof is required. Do not use the Render Beacon to create this file.
3. Identify the exact serial adapter through read-only USB/udev inspection and prove no process owns it. Merely opening common ESP32 serial adapters can toggle DTR/RTS and reset the board.
4. Preserve the currently deployed application and a full 4 MiB flash rollback image outside Git. Treat the full image as secret because NVS may contain Wi-Fi credentials. Record its SHA-256.
5. Build `sd-readonly-probe` again from the reviewed commit. Flash only `.pio/build/sd-readonly-probe/firmware.bin` using the approved deployment method; do not start a monitor.
6. Read the TFT result. PASS requires mount, recognized card type, nonzero capacity, and a readable root directory. The optional fixture must show either `NOT PRESENT (optional)` or a bounded `READ OK (N B sampled)` result; `READ FAILED` is a FAIL.
7. Test the no-card case only via a separate power-down/remove/power-up cycle. It must show bounded FAIL details without rebooting continuously. Never hot-remove the card.
8. Power down, remove the card, and verify its filesystem/image hash on the trusted reader if non-mutation evidence is required.
9. Restore the preserved production application with the approved flash procedure, then power-cycle and verify the normal Render Beacon UI and bridge polling. If restoration fails, restore the full 4 MiB rollback image and re-verify the recorded hash/image source and normal boot.

Do not run `pio run -t upload`, open serial, flash, or reset as part of repository-only qualification.

## Independent TFT reset-pin concern

The repository currently compiles both production and probe targets with `TFT_RST=12`, while the vendor definition uses `TFT_RST=-1`. GPIO12 is also configured as `TFT_MISO`. This conflict is **unresolved** and is independent of SD registration. The probe intentionally preserves the existing setting so this branch does not silently alter production display behavior. Before any physical run, compare the exact board revision schematic/vendor setup and observed reset wiring; resolve that issue in a separately reviewed hardware/firmware change if the audit confirms `-1`.
