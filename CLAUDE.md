Plan: Heltec Wireless Tracker v1.1 support
1. Hardware overview (verify against Heltec schematic before coding)
Subsystem	Part	Notes
MCU	ESP32-S3FN8 (8 MB flash) + 8 MB PSRAM (QIO)	matches V4 layout enough that the V4 PSRAM/QIO PIO config is reusable
LoRa	SX1262, no FEM	simpler than V4 — drop the GC1109/KCT8103L PA bring-up
GNSS	UC6580 (multi-constellation) on UART	new subsystem for this codebase
Display	ST7735S 0.96" 80×160 color TFT, dedicated SPI bus	not OLED; closest analogue in repo is BOARD_HELTEC_T114 (ST7789Spi on SPI1)
LED	single GPIO LED	no RGB/NeoPixel
Button	USR @ GPIO0	same as V3/V4
Power	Li-ion via VBAT divider with ADC_CTRL gate	identical pattern to V3/V4
Published Heltec pinout (must be confirmed):

LoRa SX1262: NSS=8, SCK=9, MOSI=10, MISO=11, RST=12, BUSY=13, DIO1=14 (same as V3/V4)
TFT ST7735: BL=21, DC=40, CS=38, RST=39, MOSI=42, SCK=41 (HSPI/FSPI, separate from LoRa)
VEXT (TFT + GNSS power): GPIO3, active LOW
VBAT_READ=1, ADC_CTRL=2 (matches V3/V4)
LED=18, USR_BTN=0
GNSS UART (UC6580): TX=33, RX=34, RST=35, 1PPS=36
2. Identifiers & RNode IDs
Boards.h:88-105 currently allocates 0x3F to V4 and 0x3C to T114. Add a new pair:


#define PRODUCT_HELTEC_TRACKER 0xC4   // pick next free in 0xC0-range
#define BOARD_HELTEC_TRACKER   0x3D   // pick next free 0x3D
#define MODEL_CB               0xCB   // 433 MHz variant
#define MODEL_CC               0xCC   // 868/915 MHz variant
Cross-check against Reticulum/RNode upstream rnodeconf so the IDs don't clash with anything they've allocated (this is the bigger compatibility risk than internal collisions).

3. Files to add a board branch to (mechanical)
platformio.ini — new [env:rtnode_heltec_tracker] mirroring rtnode_heltec_v4 (16 MB partitions, qio_opi PSRAM, BOARD_MODEL=BOARD_HELTEC_TRACKER), plus Adafruit GFX + Adafruit ST7735 and ST7789 in lib_deps.
Boards.h:393-459 — new #elif BOARD_MODEL == BOARD_HELTEC_TRACKER block: SX1262 pins (same as V4), HAS_DISPLAY/HAS_BLE/HAS_WIFI/HAS_PMU/HAS_INPUT/HAS_SLEEP true, drop the FEM-specific defines (LORA_PA_*, OCP_TUNED), keep DIO2_AS_RF_SWITCH, HAS_TCXO true. Add DISPLAY_* pins and TFT SPI bus pins.
Display.h:99-108 and Display.h:152-167 — new branch for the ST7735: Adafruit_ST7735 display(DISPLAY_CS, DISPLAY_DC, DISPLAY_MOSI, DISPLAY_CLK, DISPLAY_RST); with color-mode aliases (SSD1306_WHITE → ST77XX_WHITE, etc., as already done for T-Deck/T114). Audit every other BOARD_HELTEC_T114 branch in Display.h (:229, :272, :352, :416, :467, :494, :516, :533, :547, :1257, :1278) — Tracker is also a color TFT and likely needs the same treatment; T114 is the closest template.
Power.h:103-149, Power.h:205-225, Power.h:417-444 — clone the V4 battery branch (pin_vbat=1, pin_ctrl=2, scale 0.00418 — verify divider).
Utilities.h:87, :297-371, :391, :1770-1776 — add Tracker branches wherever V3/V4 are switched. Watch line 391 (TLSF/allocator gate).
BoundaryConfig.h:73 — add BOARD_HELTEC_TRACKER to the ESP32-S3 boundary-mode allowlist.
sx126x.cpp:138, :663-680 — add Tracker to the SX1262/S3 SPI bus list; treat as "no FEM" (skip the V4 PA enable/CSD/CTX sequence); IRQ wiring matches V3.
extra_script.py:118-127 — device_provision() entry for the new variant invoking rnodeconf --product c4 --model cc --hwrev 1.
4. New code
GNSS driver (new file, GNSS.h or Gps.cpp/.h): bring up VEXT, configure UART2 at 115200, parse NMEA into a small gnss_state_t (lat/lon/alt/sats/fix). Start with read-and-store; do not wire it into the LoRa packet path yet. Phase 2 can plumb it into Advertise.h for periodic position beacons.
Display init (in Display.h): TFT init sequence, backlight PWM via DISPLAY_BL_PIN, choose 80×160 rotation. Reuse the T114 page/layout system if landscape/portrait works for 80×160.
VEXT control: a small helper invoked at boot (and on wake) — also gated when sleeping to drop GNSS/TFT current.
5. Flasher / web flasher / docs (low risk, easy to forget)
flash.py:84-114 — add a "tracker" board profile. Detection (detect_board(), line ~454) currently keys on PSRAM presence to disambiguate V3 vs V4; both V4 and Tracker have PSRAM, so detection has to fall back to user selection or USB VID/PID. Simplest: add --board tracker and don't auto-detect Tracker at all in v1.
docs/index.html:415-460 — add a tracker: entry in FIRMWARE and a third option in the Detect/Select UI (mirror the v4 detect adjustment).
docs/manifest-v4-16mb.json — clone to manifest-tracker.json / manifest-tracker-full.json; point at rtnode_heltec_tracker.bin / _merged.bin.
README — update the supported-boards table (last touched in e1ee706).
6. Suggested PR sequencing
Skeleton PR: identifiers + platformio env + Boards.h block + minimal Power.h/Utilities.h/sx126x.cpp branches → builds, USB enumerates, LoRa TX/RX works headless. Don't merge anything else until this builds and a packet round-trips.
Display PR: ST7735 branch in Display.h + lib_deps; reuse T114 layout where it lines up.
GNSS PR (phase 1): VEXT + UART2 + NMEA parse, console-only output.
Flasher / docs / web flasher PR: flash.py + docs/index.html + manifest. Cuts a release once 1–3 are green.
GNSS phase 2 (optional): Advertise.h integration for position beacons.
7. Open questions to resolve before coding
Confirm whether v1.1 actually has 8 MB PSRAM (impacts QIO_OPI flags and partition layout).
Confirm exact GNSS pinout (the 33/34/35/36 set above is published but Heltec sometimes revises between board revs).
Decide whether to allocate a fresh RNode product/model ID upstream or piggy-back on the V4 IDs (the latter is faster but pollutes provisioning telemetry).
Decide GNSS scope for v1: parse-only, or beacon-on-air.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **RTNode-HeltecV4** (6192 symbols, 11356 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/RTNode-HeltecV4/context` | Codebase overview, check index freshness |
| `gitnexus://repo/RTNode-HeltecV4/clusters` | All functional areas |
| `gitnexus://repo/RTNode-HeltecV4/processes` | All execution flows |
| `gitnexus://repo/RTNode-HeltecV4/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
