#!/usr/bin/env python3
"""Offline tests; RETCN_TEST_BOOT optionally enables tests using the real stock file."""
import gzip
import importlib.util
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("retcn", Path(__file__).with_name("rtwo-retcn.py"))
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)

STOCK = b"""# Synthetic test config, not a flashable kernel
CONFIG_LOCALVERSION=""
CONFIG_LOCALVERSION_AUTO=y
CONFIG_ARM64_4K_PAGES=y
CONFIG_LTO_CLANG_FULL=y
CONFIG_CFI_CLANG=y
CONFIG_MODVERSIONS=y
CONFIG_MODULE_SIG=y
CONFIG_MODULE_SIG_PROTECT=y
CONFIG_TRIM_UNUSED_KSYMS=y
CONFIG_UNUSED_KSYMS_WHITELIST="/stock/build/path"
CONFIG_ANDROID_VENDOR_HOOKS=y
CONFIG_ANDROID_KABI_RESERVE=y
CONFIG_IKCONFIG=y
CONFIG_KALLSYMS_ALL=y
CONFIG_CLANG_VERSION=140007
CONFIG_PAHOLE_VERSION=123
CONFIG_VENDOR_SENTINEL=m
"""


def image(config, release):
    data = bytearray(64)
    data[56:60] = b"ARMd"
    return bytes(data) + b"Linux version " + release.encode() + b" (test)\0" + \
        b"IKCFG_ST" + gzip.compress(config, mtime=0) + b"IKCFG_ED"


def fixture():
    pins = r.load_pins()
    kernel = image(STOCK, pins["STOCK_KERNEL_RELEASE"])
    boot = bytearray(4 * r.PAGE)
    boot[:8] = b"ANDROID!"
    struct.pack_into("<4I", boot, 8, len(kernel), 0, 0, r.HEADER_SIZE)
    struct.pack_into("<I", boot, 40, 4)
    boot[44:54] = b"test=hello"
    boot[r.PAGE:r.PAGE + len(kernel)] = kernel
    boot[-64:-60] = b"AVBf"
    pins.update(STOCK_BOOT_SIZE=str(len(boot)), STOCK_BOOT_SHA256=r.sha256(boot),
                STOCK_IMAGE_SHA256=r.sha256(kernel), STOCK_CONFIG_SHA256=r.sha256(STOCK))
    return bytes(boot), pins


class RetcnTests(unittest.TestCase):
    def setUp(self):
        self.boot, self.pins = fixture()
        self.config = r.configure(STOCK, self.pins)
        self.image = image(self.config, self.pins["STOCK_KERNEL_RELEASE"])

    def test_pins_are_separate_from_lineage(self):
        self.assertEqual(r.load_pins()["KERNEL_REF"], "9c82b71884fdada085e6a396b7a527e1f1cdf0fc")
        lineage = (r.ROOT / "config/rtwo/pins.env").read_text()
        self.assertIn("KERNEL_VERSION=5.15.208", lineage)
        self.assertIn("LINEAGE_BOOT_SHA256=19e22bfd", lineage)
        self.assertNotIn("LINEAGE_BOOT_URL", r.PIN_FILE.read_text())
        url = r.load_pins()["STOCK_BOOT_URL"]
        self.assertEqual(url, "https://filedn.com/l6a6JS020EU70J4xNAiVtum/boot.img")
        self.assertNotIn(url, lineage)

    def test_stock_mirror_must_be_https(self):
        original = r.PIN_FILE.read_text()
        url = r.load_pins()["STOCK_BOOT_URL"]
        with tempfile.TemporaryDirectory() as tmp:
            pins_file = Path(tmp) / "pins.env"
            for invalid in ("http://mirror.example/boot.img", "https://", "https://mirror.example/a b"):
                with self.subTest(url=invalid):
                    pins_file.write_text(original.replace("STOCK_BOOT_URL=" + url,
                                                          "STOCK_BOOT_URL=" + repr(invalid)))
                    with patch.object(r, "PIN_FILE", pins_file), self.assertRaisesRegex(ValueError, "stock boot URL"):
                        r.load_pins()

    def test_ci_uses_default_mirror_without_a_repository_variable(self):
        workflow = (r.ROOT / ".github/workflows/build-rtwo-retcn.yml").read_text()
        build = workflow.split("\n  build:\n", 1)[1]
        self.assertIn("    needs: validate", build)
        self.assertNotRegex(build, r"(?m)^    if:")
        self.assertIn("RTWO_RETCN_BOOT_URL: ${{ inputs.stock_boot_url || vars.RTWO_RETCN_BOOT_URL }}", build)
        self.assertIn('${RTWO_RETCN_BOOT_URL:-$STOCK_BOOT_URL}', build)
        self.assertNotIn("Tests only:", workflow)

    def test_all_build_targets_live_on_main(self):
        for filename, entry in (("build.yml", "./scripts/build.sh"),
                                ("build-rtwo.yml", "./scripts/build-rtwo.sh"),
                                ("build-rtwo-retcn.yml", "./scripts/build-rtwo-retcn.sh")):
            with self.subTest(workflow=filename):
                workflow = (r.ROOT / ".github/workflows" / filename).read_text()
                self.assertRegex(workflow, r"(?m)^  push:\n    branches: \[main\]$")
                self.assertIn("  workflow_dispatch:", workflow)
                self.assertIn("run: " + entry, workflow)

    def test_stock_identity(self):
        _, config = r.stock_parts(self.boot, self.pins)
        self.assertEqual(config, STOCK)

    def test_wrong_firmware_or_hash_rejected(self):
        for key in ("STOCK_BOOT_SHA256", "STOCK_IMAGE_SHA256", "STOCK_CONFIG_SHA256"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "checksum mismatch"):
                r.stock_parts(self.boot, {**self.pins, key: "0" * 64})

    def test_wrong_size_rejected(self):
        with self.assertRaisesRegex(ValueError, "size mismatch"):
            r.stock_parts(self.boot[:-1], self.pins)

    def test_boot_shape_rejected(self):
        for offset, value in ((12, 1), (20, 1580), (40, 3), (1580, 1), (8, len(self.boot))):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                boot = bytearray(self.boot)
                struct.pack_into("<I", boot, offset, value)
                r.boot_kernel(boot)

    def test_wrong_boot_magic(self):
        with self.assertRaisesRegex(ValueError, "not an Android boot"):
            r.boot_kernel(b"BADMAGIC" + self.boot[8:])

    def test_arm64_image_required(self):
        with self.assertRaisesRegex(ValueError, "raw arm64"):
            r.image_config(b"x" * 64)

    def test_missing_config(self):
        with self.assertRaisesRegex(ValueError, "IKCONFIG"):
            r.image_config(self.image[:64])

    def test_false_or_corrupt_config_marker_is_skipped(self):
        image = self.image[:64] + b"IKCFG_ST\x1f\x8b\x08\x00bad-dataIKCFG_ED" + self.image[64:]
        self.assertEqual(r.image_config(image), self.config)

    def test_config_roundtrip(self):
        self.assertEqual(r.image_config(self.image), self.config)
        r.validate_config(self.config, STOCK, self.pins)

    def test_stock_config_itself_is_not_rooted(self):
        with self.assertRaises(ValueError):
            r.validate_config(STOCK, STOCK, self.pins)

    def test_reject_lkm_or_missing_susfs(self):
        for key, value in (("KSU", "m"), ("KSU_SUSFS", "n"), ("KSU_SUSFS_SUS_MAP", "n")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                r.validate_config(self.config.replace(f"CONFIG_{key}=y".encode(),
                                                     f"CONFIG_{key}={value}".encode()), STOCK, self.pins)

    def test_reject_unsafe_changes(self):
        for key in ("MODULE_FORCE_LOAD", "MODULE_SIG_FORCE", "CFI_PERMISSIVE", "KPM"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                changed = self.config.replace(f"# CONFIG_{key} is not set\n".encode(), b"")
                r.validate_config(changed + f"CONFIG_{key}=y\n".encode(), STOCK, self.pins)

    def test_reject_stock_config_drift(self):
        for key, before, after in (("MODVERSIONS", "y", "n"), ("VENDOR_SENTINEL", "m", "y"),
                                   ("CLANG_VERSION", "140007", "180000")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                r.validate_config(self.config.replace(f"CONFIG_{key}={before}".encode(),
                                                     f"CONFIG_{key}={after}".encode()), STOCK, self.pins)

    def test_pahole_probe_and_unused_whitelist_can_disappear(self):
        config = self.config.replace(b"CONFIG_PAHOLE_VERSION=123", b"CONFIG_PAHOLE_VERSION=125")
        config = config.replace(b'CONFIG_UNUSED_KSYMS_WHITELIST=""\n', b"")
        r.validate_config(config, STOCK, self.pins)

    def test_duplicate_config_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            r.config_values(self.config + b"CONFIG_KSU=y\n")

    def test_wrong_release_rejected(self):
        with self.assertRaisesRegex(ValueError, "kernel release"):
            r.check_release(image(self.config, "5.15.208-lineage"), self.pins["STOCK_KERNEL_RELEASE"])

    def test_repack_preserves_header_and_replaces_payload(self):
        for kernel in (self.image, self.image + b"longer" * 50):
            with self.subTest(size=len(kernel)):
                result = r.repack(self.boot, kernel)
                self.assertEqual(len(result), len(self.boot))
                self.assertEqual(result[:8], self.boot[:8])
                self.assertEqual(result[12:r.PAGE], self.boot[12:r.PAGE])
                self.assertEqual(r.boot_kernel(result), kernel)
                self.assertEqual(result[r.PAGE + len(kernel):], b"\0" * (len(result) - r.PAGE - len(kernel)))
                self.assertNotEqual(result[-64:-60], b"AVBf")

    def test_oversized_kernel_rejected(self):
        with self.assertRaisesRegex(ValueError, "exceeds"):
            r.repack(self.boot, self.image + b"\0" * len(self.boot))

    def test_package_and_refuse_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            base, kernel, output = tmp / "stock.img", tmp / "Image", tmp / "boot.img"
            base.write_bytes(self.boot)
            kernel.write_bytes(self.image)
            result = r.package(base, kernel, output, self.pins)
            self.assertEqual(r.sha256(output.read_bytes()), result["boot_sha256"])
            self.assertIn("unsigned", result["avb"])
            self.assertIn("not boot-tested", result["stock_module_abi"])
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                r.package(base, kernel, output, self.pins)
            self.assertEqual(base.read_bytes(), self.boot)

    def test_bad_kernel_publishes_no_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            base, kernel, output = tmp / "stock.img", tmp / "Image", tmp / "boot.img"
            base.write_bytes(self.boot)
            kernel.write_bytes(r.boot_kernel(self.boot))
            with self.assertRaises(ValueError):
                r.package(base, kernel, output, self.pins)
            self.assertFalse(output.exists())

    def test_unknown_cli_and_bash_syntax(self):
        result = subprocess.run(["python3", str(r.ROOT / "scripts/rtwo-retcn.py"), "not-a-command"],
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        subprocess.run(["bash", "-n", str(r.ROOT / "scripts/build-rtwo-retcn.sh")], check=True)

    def test_build_preflight_guards_and_verified_download_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            project, tools = tmp / "project", tmp / "bin"
            (project / "scripts").mkdir(parents=True)
            (project / "config/rtwo-retcn").mkdir(parents=True)
            tools.mkdir()
            for name in ("rtwo-retcn.py", "build-rtwo-retcn.sh"):
                shutil.copy(r.ROOT / "scripts" / name, project / "scripts" / name)
            shutil.copy(r.PIN_FILE, project / "config/rtwo-retcn/pins.env")
            for name in ("git", "make", "curl", "pahole", "bc", "bison", "flex", "cpio", "openssl"):
                tool = tools / name
                tool.write_text('#!/bin/sh\necho "UNEXPECTED_EXTERNAL_COMMAND" >&2\nexit 99\n')
                tool.chmod(0o755)
            (tools / "uname").write_text('#!/bin/sh\ncase "$1" in -s) echo Linux;; -m) echo x86_64;; esac\n')
            (tools / "uname").chmod(0o755)
            (tools / "df").write_text('#!/bin/sh\nprintf "Filesystem 1024-blocks Used Available Capacity Mounted\\nmock 100000000 0 100000000 0%% /\\n"\n')
            (tools / "df").chmod(0o755)
            bad_boot = tmp / "wrong-boot.img"
            bad_boot.write_bytes(self.boot)
            work = tmp / "existing-work"
            work.mkdir()
            marker = work / "must-survive"
            marker.write_text("untouched")
            # Clear caller build settings so this stays deterministic in CI/local shells.
            env = {k: v for k, v in os.environ.items()
                   if not k.startswith(("RTWO_", "KERNEL_BUILD_")) and k != "VARIANT"}
            env["PATH"] = str(tools) + os.pathsep + env["PATH"]
            env["LC_ALL"] = "C"
            cases = [
                ({"VARIANT": "sukisu-kpm"}, "only built-in resukisu-susfs"),
                ({"RTWO_RETCN_BOOT_URL": "http://example.invalid/boot.img"}, "HTTPS"),
                ({"RTWO_RETCN_BOOT_IMAGE": str(bad_boot)}, "stock boot size mismatch"),
                ({"RTWO_RETCN_BOOT_IMAGE": str(bad_boot), "RTWO_RETCN_WORKDIR": str(work)}, "File exists"),
                ({"RTWO_RETCN_BOOT_IMAGE": str(bad_boot), "RTWO_RETCN_ARTIFACT_DIR": str(work)}, "artifact directory already exists"),
            ]
            for settings, message in cases:
                with self.subTest(settings=settings):
                    result = subprocess.run(["bash", str(project / "scripts/build-rtwo-retcn.sh")],
                                            env={**env, **settings}, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(message, result.stderr)
                    self.assertNotIn("UNEXPECTED_EXTERNAL_COMMAND", result.stderr)
            self.assertEqual(marker.read_text(), "untouched")
            self.assertEqual(bad_boot.read_bytes(), self.boot)

            # Exercise URL precedence without a real download or source sync.
            # Even a successful transfer must fail on the wrong firmware bytes.
            curl_log = tmp / "curl-args"
            (tools / "curl").write_text('''#!/bin/sh
printf '%s\\n' "$@" > "$TEST_CURL_ARGS"
while [ "$#" -gt 0 ]; do
  if [ "$1" = --output ]; then
    cp -- "$TEST_BAD_BOOT" "$2"
    exit 0
  fi
  shift
done
exit 98
''')
            env.update(TEST_CURL_ARGS=str(curl_log), TEST_BAD_BOOT=str(bad_boot))
            default_url = r.load_pins()["STOCK_BOOT_URL"]
            for settings, expected_url in (
                ({}, default_url),
                ({"RTWO_RETCN_BOOT_URL": ""}, default_url),
                ({"RTWO_RETCN_BOOT_URL": "https://mirror.example/boot.img"}, "https://mirror.example/boot.img"),
            ):
                with self.subTest(download=settings):
                    result = subprocess.run(["bash", str(project / "scripts/build-rtwo-retcn.sh")],
                                            env={**env, **settings}, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("stock boot size mismatch", result.stderr)
                    self.assertNotIn("UNEXPECTED_EXTERNAL_COMMAND", result.stderr)
                    args = curl_log.read_text().splitlines()
                    self.assertEqual(args[-1], expected_url)
                    self.assertEqual(args[args.index("--max-filesize") + 1], "100663296")

    @unittest.skipUnless(os.environ.get("RETCN_TEST_BOOT"), "optional stock firmware fixture not supplied")
    def test_real_stock_firmware(self):
        boot = Path(os.environ["RETCN_TEST_BOOT"]).read_bytes()
        pins = r.load_pins()
        kernel, stock = r.stock_parts(boot, pins)
        config = r.configure(stock, pins)
        r.validate_config(config, stock, pins)
        result = r.repack(boot, kernel)
        self.assertEqual(r.boot_kernel(result), kernel)
        self.assertEqual(len(result), 96 * 1024 * 1024)
        self.assertNotEqual(result[-64:-60], b"AVBf")
        # A stock roundtrip is only a structural test, not a rooted build artifact.
        with self.assertRaises(ValueError):
            r.validate_config(r.image_config(kernel), stock, pins)


if __name__ == "__main__":
    unittest.main(verbosity=2)
