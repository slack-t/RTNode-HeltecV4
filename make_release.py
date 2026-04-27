#!/usr/bin/env python3
"""
make_release.py — RTNode-HeltecV4 Release Packager

Builds all firmware variants with PlatformIO, merges each into a self-contained
binary (bootloader + partitions + boot_app0 + app), and packages everything
into a single ZIP archive ready for publishing on GitHub Releases.

The resulting archive contains:
  - flash.py                         (flasher utility — no PlatformIO required to use)
  - README.md                        (release notes / instructions)
  - rnode_firmware_*_merged.bin      (one pre-merged binary per board/flash-size)
  - esptool/<binary>                 (added separately — platform-specific standalone binary)

Usage:
  python make_release.py                   # build all envs and package
  python make_release.py --no-build        # skip pio build (use existing .pio/build output)
  python make_release.py --output myfile.zip

The standalone esptool binary is NOT included automatically (it is platform-specific).
Copy it to Release/esptool/esptool (or esptool/esptool) before or after running
this script, then add it to the ZIP manually or via CI.
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile

# ── Configuration ──────────────────────────────────────────────────────────────

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
OUTPUT_ZIP   = os.path.join(SCRIPT_DIR, "Release", "rtnode_firmware.zip")

# Environments to build and merge. Maps PIO env → merged binary name.
BUILD_ENVS = [
    {
        "pio_env":      "heltec_V3_boundary",
        "build_dir":    ".pio/build/heltec_V3_boundary",
        "firmware_bin": "rnode_firmware_heltec32v3.bin",
        "merged_bin":   "rnode_firmware_heltec32v3_merged.bin",
        "chip":         "esp32s3",
        "flash_mode":   "dio",
        "flash_freq":   "80m",
        "flash_size":   "8MB",
        "board_name":   "Heltec V3 (8MB)",
    },
    {
        "pio_env":      "heltec_V4_boundary",
        "build_dir":    ".pio/build/heltec_V4_boundary",
        "firmware_bin": "rnode_firmware_heltec32v4_boundary_8mb.bin",
        "merged_bin":   "rnode_firmware_heltec32v4_boundary_8mb_merged.bin",
        "chip":         "esp32s3",
        "flash_mode":   "qio",
        "flash_freq":   "80m",
        "flash_size":   "8MB",
        "board_name":   "Heltec V4 (8MB)",
    },
    {
        "pio_env":      "heltec_V4_boundary_16mb",
        "build_dir":    ".pio/build/heltec_V4_boundary_16mb",
        "firmware_bin": "rnode_firmware_heltec32v4_boundary_16mb.bin",
        "merged_bin":   "rnode_firmware_heltec32v4_boundary_16mb_merged.bin",
        "chip":         "esp32s3",
        "flash_mode":   "qio",
        "flash_freq":   "80m",
        "flash_size":   "16MB",
        "board_name":   "Heltec V4 (16MB)",
    },
]

# Boot component addresses
BOOTLOADER_ADDR = 0x0000
PARTITIONS_ADDR = 0x8000
BOOT_APP0_ADDR  = 0xe000
APP_ADDR        = 0x10000

# Files included in the ZIP alongside the firmware binaries
EXTRA_FILES = [
    ("flash.py",   "flash.py"),
    ("README.md",  "README.md"),
]


def find_pio():
    """Return the PlatformIO executable path."""
    for name in ("pio", "platformio"):
        path = shutil.which(name)
        if path:
            return path
    return None


def find_esptool():
    """Return an esptool command list for merge_bin operations."""
    exe_name = "esptool.exe" if platform.system() == "Windows" else "esptool"

    # Prefer PlatformIO bundled esptool (always present when PIO is installed)
    candidates = [
        os.path.expanduser("~/.platformio/packages/tool-esptoolpy/esptool.py"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return [sys.executable, c]

    # Fall back to system esptool
    for name in ("esptool.py", "esptool"):
        if shutil.which(name):
            return [name]

    # Release standalone binary
    for rel in (os.path.join(SCRIPT_DIR, "esptool", exe_name),
                os.path.join(SCRIPT_DIR, "Release", "esptool", exe_name)):
        if os.path.isfile(rel) and os.access(rel, os.X_OK):
            return [rel]

    return None


def find_boot_app0():
    """Return path to boot_app0.bin from PlatformIO framework."""
    pio_dir = os.path.expanduser("~/.platformio/packages")
    if os.path.isdir(pio_dir):
        for name in sorted(os.listdir(pio_dir), reverse=True):
            if name.startswith("framework-arduinoespressif32"):
                candidate = os.path.join(pio_dir, name, "tools", "partitions", "boot_app0.bin")
                if os.path.isfile(candidate):
                    return candidate
    return None


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_env(env_cfg, pio):
    """Run `pio run -e <env>` and return True on success."""
    env = env_cfg["pio_env"]
    print(f"\n  Building {env_cfg['board_name']} (env: {env})...")
    result = subprocess.run([pio, "run", "-e", env], cwd=SCRIPT_DIR)
    if result.returncode != 0:
        print(f"  ERROR: Build failed for {env}")
        return False
    fw = os.path.join(SCRIPT_DIR, env_cfg["build_dir"], env_cfg["firmware_bin"])
    if not os.path.isfile(fw):
        print(f"  ERROR: Firmware not found after build: {fw}")
        return False
    print(f"  OK: {fw}")
    return True


def merge_env(env_cfg, esptool_cmd, boot_app0):
    """Merge bootloader + partitions + boot_app0 + app into a single binary."""
    build_dir   = os.path.join(SCRIPT_DIR, env_cfg["build_dir"])
    bootloader  = os.path.join(build_dir, "bootloader.bin")
    partitions  = os.path.join(build_dir, "partitions.bin")
    firmware    = os.path.join(build_dir, env_cfg["firmware_bin"])
    merged_out  = os.path.join(build_dir, env_cfg["merged_bin"])

    missing = []
    if not os.path.isfile(bootloader):  missing.append(bootloader)
    if not os.path.isfile(partitions):  missing.append(partitions)
    if not boot_app0:                   missing.append("boot_app0.bin (not found)")
    if not os.path.isfile(firmware):    missing.append(firmware)
    if missing:
        print(f"  ERROR: Missing components for {env_cfg['board_name']}:")
        for m in missing:
            print(f"    {m}")
        return None

    print(f"  Merging {env_cfg['board_name']}...")
    cmd = esptool_cmd + [
        "--chip", env_cfg["chip"],
        "merge_bin",
        "--flash_mode", env_cfg["flash_mode"],
        "--flash_freq", env_cfg["flash_freq"],
        "--flash_size", env_cfg["flash_size"],
        "-o", merged_out,
        f"0x{BOOTLOADER_ADDR:x}", bootloader,
        f"0x{PARTITIONS_ADDR:x}", partitions,
        f"0x{BOOT_APP0_ADDR:x}",  boot_app0,
        f"0x{APP_ADDR:x}",        firmware,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=SCRIPT_DIR)
    if result.returncode != 0:
        print(f"  ERROR: merge_bin failed:\n{result.stderr}{result.stdout}")
        return None

    size = os.path.getsize(merged_out)
    digest = sha256_file(merged_out)
    print(f"  OK: {env_cfg['merged_bin']}  ({size:,} bytes)  sha256:{digest[:16]}...")
    return merged_out


def read_version():
    """Read MAJ_VERS + MIN_VERS from Config.h."""
    cfg = os.path.join(SCRIPT_DIR, "Config.h")
    maj = min_ = None
    try:
        with open(cfg, "rb") as f:
            for raw in f:
                line = raw.decode("utf-8", errors="replace").strip()
                if line.startswith("#define MAJ_VERS"):
                    maj = "%01d" % ord(bytes.fromhex(line.split()[2].split("x")[1]))
                if line.startswith("#define MIN_VERS"):
                    min_ = "%02d" % ord(bytes.fromhex(line.split()[2].split("x")[1]))
    except Exception as e:
        print(f"Warning: could not read version from Config.h: {e}")
    return f"{maj}.{min_}" if maj and min_ else "unknown"


def create_zip(merged_paths, output_path):
    """Create the release ZIP from merged binaries and extra files."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for merged in merged_paths:
            arcname = os.path.basename(merged)
            zf.write(merged, arcname)
            print(f"  + {arcname}")
        for local_path, arcname in EXTRA_FILES:
            full = os.path.join(SCRIPT_DIR, local_path)
            if os.path.isfile(full):
                zf.write(full, arcname)
                print(f"  + {arcname}")
            else:
                print(f"  (skip {arcname} — not found)")
    size = os.path.getsize(output_path)
    digest = sha256_file(output_path)
    print(f"\n  Archive: {output_path}")
    print(f"  Size:    {size:,} bytes")
    print(f"  SHA-256: {digest}")
    return digest


def write_hashes(merged_paths, version, output_dir):
    """Write release_hashes.json next to the ZIP."""
    hashes = {}
    for path in merged_paths:
        name = os.path.basename(path)
        hashes[name] = {
            "hash":    sha256_file(path),
            "version": version,
        }
    out = os.path.join(output_dir, "release_hashes.json")
    with open(out, "w") as f:
        json.dump(hashes, f, indent=2)
    print(f"\n  Hashes written: {out}")


def main():
    parser = argparse.ArgumentParser(description="Build and package RTNode firmware release")
    parser.add_argument("--no-build", action="store_true",
                        help="Skip PlatformIO build — use existing .pio/build output")
    parser.add_argument("--output", "-o", default=OUTPUT_ZIP,
                        help=f"Output ZIP path (default: {OUTPUT_ZIP})")
    parser.add_argument("--envs", nargs="*",
                        help="Limit to specific PIO envs (e.g. heltec_V3_boundary)")
    args = parser.parse_args()

    version = read_version()
    print(f"\nRTNode Release Packager — firmware version {version}")
    print("=" * 55)

    # Filter envs if requested
    envs = BUILD_ENVS
    if args.envs:
        envs = [e for e in BUILD_ENVS if e["pio_env"] in args.envs]
        if not envs:
            print(f"Error: no matching envs in {args.envs}")
            sys.exit(1)

    pio = find_pio()
    esptool_cmd = find_esptool()
    boot_app0 = find_boot_app0()

    if not pio and not args.no_build:
        print("Error: PlatformIO not found. Install from https://platformio.org")
        print("Or use --no-build to skip the build step.")
        sys.exit(1)

    if not esptool_cmd:
        print("Error: esptool not found. Install with: pip install esptool")
        sys.exit(1)

    if not boot_app0:
        print("Error: boot_app0.bin not found in PlatformIO packages.")
        print("Run 'pio run -e heltec_V3_boundary' once to install framework packages.")
        sys.exit(1)

    print(f"\nPlatformIO:  {pio or '(skipped)'}")
    print(f"esptool:     {' '.join(esptool_cmd)}")
    print(f"boot_app0:   {boot_app0}")
    print(f"Output ZIP:  {args.output}")

    # ── Build ──────────────────────────────────────────────────────────────
    if not args.no_build:
        print("\n── Build ──────────────────────────────────────────────────────────")
        for env_cfg in envs:
            if not build_env(env_cfg, pio):
                sys.exit(1)
    else:
        print("\n(--no-build: skipping PlatformIO build)")

    # ── Merge ──────────────────────────────────────────────────────────────
    print("\n── Merge ──────────────────────────────────────────────────────────")
    merged_paths = []
    for env_cfg in envs:
        path = merge_env(env_cfg, esptool_cmd, boot_app0)
        if not path:
            sys.exit(1)
        merged_paths.append(path)

    # ── Package ────────────────────────────────────────────────────────────
    print("\n── Package ────────────────────────────────────────────────────────")
    digest = create_zip(merged_paths, args.output)

    write_hashes(merged_paths, version, os.path.dirname(args.output))

    print()
    print("╔══════════════════════════════════════════╗")
    print("║     Release package complete!            ║")
    print("╚══════════════════════════════════════════╝")
    print()
    print("Next steps:")
    print(f"  1. Copy the platform-specific esptool binary into the ZIP:")
    print(f"       Release/esptool/esptool  (macOS / Linux)")
    print(f"       Release/esptool/esptool.exe  (Windows)")
    print(f"     Or add it to the ZIP:  zip {args.output} Release/esptool/esptool")
    print(f"  2. Upload {args.output} to GitHub Releases as '{os.path.basename(args.output)}'")
    print(f"  3. Tag the release — flash.py will detect and download it automatically")


if __name__ == "__main__":
    main()
