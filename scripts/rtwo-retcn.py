#!/usr/bin/env python3
"""File-only, exact-baseline RETCN configuration and unsigned boot-v4 packaging."""
from __future__ import annotations

import argparse
import difflib
import gzip
import hashlib
import json
from pathlib import Path
import re
import shlex
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
PIN_FILE = ROOT / "config/rtwo-retcn/pins.env"
PAGE = 4096
HEADER_SIZE = 1584


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_pins() -> dict[str, str]:
    pins = {}
    for line in PIN_FILE.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        words = shlex.split(value)
        require(len(words) == 1 and key not in pins, f"invalid/duplicate pin: {key}")
        pins[key] = words[0]
    for key in ("KERNEL_REF", "CLANG_REF", "NDK_REF", "RESUKISU_REF", "SUSFS_REF"):
        require(bool(re.fullmatch(r"[0-9a-f]{40}", pins[key])), f"invalid {key}")
    for key in ("STOCK_BOOT_SHA256", "STOCK_IMAGE_SHA256", "STOCK_CONFIG_SHA256"):
        require(bool(re.fullmatch(r"[0-9a-f]{64}", pins[key])), f"invalid {key}")
    require(bool(re.fullmatch(r"https://[^\s]+", pins["STOCK_BOOT_URL"])), "invalid stock boot URL")
    require(pins["ANDROID_KERNEL_FAMILY"] == "android13-5.15", "wrong kernel family")
    require(pins["KMI_GENERATION"] == "8", "wrong KMI generation")
    require(pins["NDK_VERSION"] == "23.1.7779620", "wrong NDK version")
    require(pins["NDK_SYSROOT"] == "toolchains/llvm/prebuilt/linux-x86_64/sysroot", "wrong NDK sysroot")
    require(pins["NDK_TRIPLE"] == "aarch64-linux-android31", "wrong NDK userspace target")
    require(pins["DEVICE"] == "rtwo", "wrong device")
    require(pins["STOCK_KERNEL_RELEASE"].startswith(pins["KERNEL_VERSION"] + "-android13-8-"),
            "inconsistent kernel release")
    require(int(pins["STOCK_BOOT_SIZE"]) == 96 * 1024 * 1024, "wrong partition size")
    require(pins["SUSFS_PATCH"] == "kernel_patches/50_add_susfs_in_gki-android13-5.15.patch",
            "wrong SUSFS patch family")
    return pins


def boot_kernel(boot: bytes) -> bytes:
    require(len(boot) >= PAGE and boot[:8] == b"ANDROID!", "not an Android boot image")
    kernel_size, ramdisk_size, _, header_size = struct.unpack_from("<4I", boot, 8)
    require(struct.unpack_from("<I", boot, 40)[0] == 4 and header_size == HEADER_SIZE,
            "expected boot header v4")
    require(ramdisk_size == 0, "RETCN boot must not contain a ramdisk")
    require(struct.unpack_from("<I", boot, 1580)[0] == 0, "unexpected boot signature")
    require(64 <= kernel_size <= len(boot) - PAGE, "invalid/truncated kernel payload")
    return boot[PAGE:PAGE + kernel_size]


def image_config(image: bytes) -> bytes:
    require(len(image) >= 64 and image[56:60] == b"ARMd", "expected raw arm64 Image")
    offset = 0
    while (start := image.find(b"IKCFG_ST", offset)) >= 0:
        end = image.find(b"IKCFG_ED", start + 8)
        if end < 0:
            break
        try:
            config = gzip.decompress(image[start + 8:end])
        except (OSError, EOFError, zlib.error):
            offset = start + 8
            continue
        if config.startswith(b"#") and b"CONFIG_" in config:
            return config
        offset = start + 8
    raise ValueError("Image has no embedded IKCONFIG")


def check_release(image: bytes, release: str) -> None:
    require(bool(re.search(rb"Linux version " + re.escape(release.encode()) + rb" \(", image)),
            "Image kernel release does not match the RETCN baseline")


def stock_parts(boot: bytes, pins: dict[str, str]) -> tuple[bytes, bytes]:
    require(len(boot) == int(pins["STOCK_BOOT_SIZE"]), "stock boot size mismatch")
    require(sha256(boot) == pins["STOCK_BOOT_SHA256"], "stock boot checksum mismatch")
    image = boot_kernel(boot)
    require(sha256(image) == pins["STOCK_IMAGE_SHA256"], "stock Image checksum mismatch")
    check_release(image, pins["STOCK_KERNEL_RELEASE"])
    config = image_config(image)
    require(sha256(config) == pins["STOCK_CONFIG_SHA256"], "stock config checksum mismatch")
    require(boot[-64:-60] == b"AVBf", "stock AVB footer missing")
    return image, config


def config_values(config: bytes) -> dict[str, str]:
    result = {}
    for line in config.decode().splitlines():
        if match := re.fullmatch(r"(CONFIG_\w+)=(.*)", line):
            key, value = match.groups()
        elif match := re.fullmatch(r"# (CONFIG_\w+) is not set", line):
            key, value = match[1], "n"
        else:
            continue
        require(key not in result, f"duplicate config key: {key}")
        result[key] = value
    return result


def changes(pins: dict[str, str]) -> dict[str, str]:
    suffix = pins["STOCK_KERNEL_RELEASE"].removeprefix(pins["KERNEL_VERSION"])
    return {
        # Retain module directory/release expectations, not an assertion of OEM authenticity.
        "CONFIG_LOCALVERSION": json.dumps(suffix),
        "CONFIG_LOCALVERSION_AUTO": "n",
        # Stock GKI modules are signed with another key. Preserve verification and CRCs,
        # but do not enforce protected exports or trim exports needed by root/stock modules.
        "CONFIG_MODULE_SIG_PROTECT": "n",
        "CONFIG_TRIM_UNUSED_KSYMS": "n",
        "CONFIG_UNUSED_KSYMS_WHITELIST": '""',
        "CONFIG_KSU": "y",
        "CONFIG_KSU_SUSFS": "y",
        "CONFIG_KSU_DEBUG": "n",
        "CONFIG_KSU_DISABLE_MANAGER": "n",
        "CONFIG_KSU_DISABLE_POLICY": "n",
        "CONFIG_KSU_TOOLKIT_SUPPORT": "n",
        "CONFIG_KSU_TRACEPOINT_HOOK": "n",
        "CONFIG_KSU_MANUAL_HOOK": "n",
        "CONFIG_KPM": "n",
        **{f"CONFIG_KSU_SUSFS_{key}": "y" for key in (
            "SUS_PATH", "SUS_MOUNT", "SUS_KSTAT", "SPOOF_UNAME", "ENABLE_LOG",
            "HIDE_KSU_SUSFS_SYMBOLS", "SPOOF_CMDLINE_OR_BOOTCONFIG", "OPEN_REDIRECT", "SUS_MAP",
        )},
    }


def configure(stock: bytes, pins: dict[str, str]) -> bytes:
    require(sha256(stock) == pins["STOCK_CONFIG_SHA256"], "stock config checksum mismatch")
    values = config_values(stock)
    values.update(changes(pins))
    return ("# RETCN stock IKCONFIG with reviewed built-in ReSukiSU/SUSFS changes\n" +
            "\n".join(f"# {key} is not set" if value == "n" else f"{key}={value}"
                      for key, value in values.items()) + "\n").encode()


def validate_config(config: bytes, stock: bytes, pins: dict[str, str]) -> None:
    require(sha256(stock) == pins["STOCK_CONFIG_SHA256"], "stock config checksum mismatch")
    actual, baseline = config_values(config), config_values(stock)
    expected = changes(pins)
    for key, value in expected.items():
        if key == "CONFIG_UNUSED_KSYMS_WHITELIST":  # Disappears when trimming is disabled.
            require(actual.get(key, '""') == '""', "stale symbol whitelist")
        else:
            require(actual.get(key, "n") == value, f"unexpected {key}; expected {value}")
    for key in (
        "CONFIG_ARM64_4K_PAGES", "CONFIG_LTO_CLANG_FULL", "CONFIG_CFI_CLANG",
        "CONFIG_MODVERSIONS", "CONFIG_MODULE_SIG", "CONFIG_ANDROID_VENDOR_HOOKS",
        "CONFIG_ANDROID_KABI_RESERVE", "CONFIG_IKCONFIG", "CONFIG_KALLSYMS_ALL",
    ):
        require(actual.get(key) == "y", f"required stock setting missing: {key}")
    for key in ("CONFIG_MODULE_SIG_FORCE", "CONFIG_MODULE_FORCE_LOAD", "CONFIG_CFI_PERMISSIVE"):
        require(actual.get(key, "n") == "n", f"unsafe setting: {key}")
    # Fail closed on unexpected stock drift, including compiler/LTO/module settings.
    # Host pahole version/capability probes may differ; retain the complete diff in artifacts.
    for key in baseline.keys() | actual.keys():
        if key in expected or key.startswith(("CONFIG_KSU_", "CONFIG_PAHOLE_")):
            continue
        require(actual.get(key, "n") == baseline.get(key, "n"), f"unreviewed stock config drift: {key}")


def repack(boot: bytes, image: bytes) -> bytes:
    """Specific zero-ramdisk v4 layout; intentionally remove obsolete OEM AVB data."""
    boot_kernel(boot)
    image_config(image)
    require(PAGE + len(image) <= len(boot), "replacement kernel exceeds boot partition")
    result = bytearray(len(boot))
    result[:PAGE] = boot[:PAGE]
    struct.pack_into("<I", result, 8, len(image))
    result[PAGE:PAGE + len(image)] = image
    # Fresh zero padding, NOT the old payload, vbmeta signature, footer or recovery data.
    require(boot_kernel(result) == image, "repacked payload verification failed")
    return bytes(result)


def package(base: Path, image_path: Path, output: Path, pins: dict[str, str]) -> dict:
    require(not output.exists(), f"refusing to overwrite {output}")
    boot, image = base.read_bytes(), image_path.read_bytes()
    _, stock = stock_parts(boot, pins)
    config = image_config(image)
    validate_config(config, stock, pins)
    check_release(image, pins["STOCK_KERNEL_RELEASE"])
    result = repack(boot, image)
    with output.open("xb") as stream:
        stream.write(result)
    return {
        "target": "rtwo-retcn", "variant": "resukisu-susfs", "pins": pins,
        "image_sha256": sha256(image), "config_sha256": sha256(config),
        "boot_sha256": sha256(result), "boot_size": len(result),
        "avb": "unsigned; old OEM vbmeta/footer removed; unlocked bootloader required",
        "stock_module_abi": "not boot-tested; symbol CRC/module-load validation still required",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-pins")
    extract = sub.add_parser("extract")
    extract.add_argument("boot", type=Path)
    extract.add_argument("destination", type=Path)
    config = sub.add_parser("configure")
    config.add_argument("stock", type=Path)
    config.add_argument("output", type=Path)
    check = sub.add_parser("validate-config")
    check.add_argument("stock", type=Path)
    check.add_argument("config", type=Path)
    pack = sub.add_parser("package")
    pack.add_argument("boot", type=Path)
    pack.add_argument("image", type=Path)
    pack.add_argument("destination", type=Path)
    args = parser.parse_args()
    pins = load_pins()
    if args.command == "verify-pins":
        print(f"validated stock RETCN pins for {pins['STOCK_KERNEL_RELEASE']}")
    elif args.command == "extract":
        image, config = stock_parts(args.boot.read_bytes(), pins)
        args.destination.mkdir(parents=True, exist_ok=False)
        (args.destination / "Image").write_bytes(image)
        (args.destination / "kernel.config").write_bytes(config)
    elif args.command == "configure":
        require(args.output.resolve() != args.stock.resolve(), "cannot overwrite the stock config")
        args.output.write_bytes(configure(args.stock.read_bytes(), pins))
    elif args.command == "validate-config":
        validate_config(args.config.read_bytes(), args.stock.read_bytes(), pins)
        print("validated built-in root/SUSFS, stock config and intentional policy changes")
    elif args.command == "package":
        args.destination.mkdir(parents=True, exist_ok=True)
        filename = "boot-retcn-susfs.img"
        metadata = package(args.boot, args.image, args.destination / filename, pins)
        (args.destination / (filename + ".sha256")).write_text(f"{metadata['boot_sha256']}  {filename}\n")
        (args.destination / "boot-metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        _, stock = stock_parts(args.boot.read_bytes(), pins)
        config = image_config(args.image.read_bytes())
        (args.destination / "kernel.config").write_bytes(config)
        (args.destination / "config.diff").write_text("".join(difflib.unified_diff(
            stock.decode().splitlines(True), config.decode().splitlines(True),
            fromfile="stock-RETCN.config", tofile="built-RETCN-SUSFS.config")))
        print(f"packaged {args.destination / filename} (unsigned, boot testing pending)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError) as error:
        sys.exit(f"RETCN validation failed: {error}")
