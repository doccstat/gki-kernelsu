#!/usr/bin/env python3
"""Offline tests; RETCN_TEST_BOOT optionally enables tests using the real stock file."""
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
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
CONFIG_MODULE_SCMVERSION=y
CONFIG_MODULE_SIG_PROTECT=y
CONFIG_TRIM_UNUSED_KSYMS=y
CONFIG_UNUSED_KSYMS_WHITELIST="/stock/build/path"
CONFIG_ANDROID_VENDOR_HOOKS=y
CONFIG_ANDROID_KABI_RESERVE=y
CONFIG_IKCONFIG=y
CONFIG_KALLSYMS_ALL=y
CONFIG_CLANG_VERSION=140007
CONFIG_CC_CAN_LINK=y
CONFIG_CC_CAN_LINK_STATIC=y
CONFIG_UAPI_HEADER_TEST=y
CONFIG_HEADERS_INSTALL=y
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
        self.assertIn("KERNEL_VERSION=5.15.211", lineage)
        self.assertIn("LINEAGE_BOOT_SHA256=60fbd11a", lineage)
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

    def test_ndk_pins_reject_wrong_sysroot_or_target(self):
        original = r.PIN_FILE.read_text()
        pins = r.load_pins()
        with tempfile.TemporaryDirectory() as tmp:
            pin_file = Path(tmp) / "pins.env"
            for key, value in (("NDK_REF", "not-a-commit"), ("NDK_VERSION", "25.0"),
                               ("NDK_SYSROOT", "toolchains/wrong/sysroot"),
                               ("NDK_TRIPLE", "x86_64-linux-android31")):
                with self.subTest(pin=key):
                    pin_file.write_text(original.replace(key + "=" + pins[key], key + "=" + value))
                    with patch.object(r, "PIN_FILE", pin_file), self.assertRaisesRegex(ValueError, "NDK"):
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

    def test_stock_versioning_settings_are_preserved(self):
        values = r.config_values(self.config)
        self.assertEqual(values["CONFIG_LOCALVERSION"], '""')
        self.assertEqual(values["CONFIG_LOCALVERSION_AUTO"], "y")
        self.assertEqual(values["CONFIG_MODULE_SCMVERSION"], "y")
        for key in ("CONFIG_LOCALVERSION_AUTO", "CONFIG_MODULE_SCMVERSION"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, key):
                r.validate_config(self.config.replace((key + "=y\n").encode(), b""), STOCK, self.pins)

    def test_reports_all_unreviewed_drift_in_stable_order(self):
        config = self.config.replace(b"CONFIG_VENDOR_SENTINEL=m", b"CONFIG_VENDOR_SENTINEL=y")
        config = config.replace(b"CONFIG_CC_CAN_LINK=y", b"# CONFIG_CC_CAN_LINK is not set")
        with self.assertRaisesRegex(ValueError, "unreviewed stock config drift: CONFIG_CC_CAN_LINK, CONFIG_VENDOR_SENTINEL$"):
            r.validate_config(config, STOCK, self.pins)

    def test_upstream_setlocalversion_uses_cache_without_git_or_duplicate_suffix(self):
        script = r.ROOT / "config/rtwo-retcn/tests/setlocalversion"
        self.assertEqual(r.sha256(script.read_bytes()),
                         "3cdcdf4cb607cacf4ec5a5834d85a16d8cbae48d82f13d7fa7e20ab59469581a")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, build, tools = root / "common", root / "build", root / "bin"
            source.mkdir(); tools.mkdir()
            (build / "include/config").mkdir(parents=True)
            values = r.config_values(self.config)
            (build / "include/config/auto.conf").write_text("".join(
                f"{key}={values[key]}\n" for key in ("CONFIG_LOCALVERSION", "CONFIG_LOCALVERSION_AUTO")))
            log = root / "git.log"
            git = tools / "git"
            git.write_text('#!/bin/sh\necho "$*" >> "$TEST_GIT_LOG"\n'
                           'case "$*" in\n'
                           '  "rev-parse --verify HEAD") echo ' + self.pins["KERNEL_REF"] + ';;\n'
                           '  "--no-optional-locks status -uno --porcelain") echo " M fs/susfs.c";;\n'
                           '  *) exit 1;;\nesac\n')
            git.chmod(0o755)
            env = {**os.environ, "PATH": str(tools) + os.pathsep + os.environ["PATH"],
                   "LOCALVERSION": "", "BUILD_NUMBER": "", "TEST_GIT_LOG": str(log)}
            suffix = self.pins["STOCK_KERNEL_RELEASE"].removeprefix(self.pins["KERNEL_VERSION"])
            (source / ".scmversion").write_text(suffix + "\n")
            for option in ([], ["--save-tag"]):
                with self.subTest(option=option):
                    result = subprocess.run(["sh", str(script), *option, str(source), "android13-5.15", "8"],
                                            cwd=build, env=env, text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.pins["KERNEL_VERSION"] + result.stdout.strip(),
                                     self.pins["STOCK_KERNEL_RELEASE"])
                    self.assertFalse(log.exists(), "SCM cache must bypass Git's dirty/shallow-tree suffix")
            (source / ".scmversion").unlink()
            result = subprocess.run(["sh", str(script), str(source), "android13-5.15", "8"],
                                    cwd=build, env=env, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("-dirty", result.stdout)
            self.assertNotEqual(self.pins["KERNEL_VERSION"] + result.stdout.strip(), self.pins["STOCK_KERNEL_RELEASE"])
            self.assertTrue(log.exists())

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
                                   ("CLANG_VERSION", "140007", "180000"),
                                   ("CC_CAN_LINK", "y", "n"), ("CC_CAN_LINK_STATIC", "y", "n"),
                                   ("UAPI_HEADER_TEST", "y", "n"), ("HEADERS_INSTALL", "y", "n")):
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


class UserspaceBuildTests(unittest.TestCase):
    """Run the actual build shell with mocked downloads/compiler/make, not a kernel build."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "project"
        (self.project / "scripts").mkdir(parents=True)
        shutil.copytree(r.ROOT / "config/rtwo-retcn", self.project / "config/rtwo-retcn")
        shutil.copy2(r.ROOT / "scripts/build-rtwo-retcn.sh", self.project / "scripts/build-rtwo-retcn.sh")
        shutil.copy2(r.ROOT / "scripts/rtwo-retcn.py", self.project / "scripts/retcnlib.py")
        self.tools, self.seed = self.root / "bin", self.root / "seed"
        self.tools.mkdir()
        self.seed.mkdir()
        self.log = self.root / "calls.jsonl"
        self.stock = self.root / "stock.config"
        self.stock.write_bytes(STOCK)
        self.pins = r.load_pins()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("RTWO_", "KERNEL_BUILD_")) and key not in ("VARIANT", "USERCFLAGS", "USERLDFLAGS")}
        self.env.update(PATH=str(self.tools) + os.pathsep + os.environ["PATH"],
                        RTWO_RETCN_BOOT_IMAGE=str(self.stock), TEST_STOCK_CONFIG=str(self.stock),
                        TEST_LOG=str(self.log), TEST_SEED=str(self.seed),
                        TEST_KERNEL_VERSION=self.pins["KERNEL_VERSION"],
                        USERCFLAGS="untrusted-inherited-flags", USERLDFLAGS="untrusted-inherited-flags")
        self.attempt = 0

        def seed_file(path, text):
            output = self.seed / path
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text)

        seed_file("common/build.config.common", "KMI_GENERATION=8\n")
        seed_file("common/build.config.constants", "CLANG_VERSION=r450784e\n")
        seed_file("common/build.config.aarch64", "NDK_TRIPLE=aarch64-linux-android31\n")
        seed_file("common/drivers/Kconfig", 'menu "drivers"\nendmenu\n')
        seed_file("common/drivers/Makefile", "# mock\n")
        for path in ("common/fs", "common/include/linux", "ReSukiSU/kernel"):
            (self.seed / path).mkdir(parents=True)
        seed_file("susfs4ksu/" + self.pins["SUSFS_PATCH"], "# mock patch\n")
        seed_file("susfs4ksu/kernel_patches/fs/susfs.c", "/* mock */\n")
        seed_file("susfs4ksu/kernel_patches/include/linux/susfs.h", "/* mock */\n")
        seed_file("ndk/source.properties", "Pkg.Revision = 23.1.7779620\n")
        seed_file("ndk/" + self.pins["NDK_SYSROOT"] + "/usr/include/stdio.h", "/* mock */\n")
        # Same invocation shape as the pinned scripts/cc-can-link.sh; mocked
        # clang verifies that the actual build shell supplies the missing flags.
        seed_file("common/scripts/cc-can-link.sh", '#!/bin/sh\nprintf "int main(void) {}\\n" | "$@" -x c - -o /dev/null >/dev/null 2>&1\n')
        (self.seed / "common/scripts/cc-can-link.sh").chmod(0o755)
        self.tool(self.seed / "clang" / self.pins["CLANG_DIR"] / "bin/clang", "clang", '''
if args == ['--version']:
    print('Android (8508608, based on r450784e) clang version 14.0.7')
    sys.exit(0)
sys.stdin.read()
assert '--target=aarch64-linux-android31' in args
assert '-fuse-ld=lld' in args and '--rtlib=compiler-rt' in args
sysroot = Path(next(arg.split('=', 1)[1] for arg in args if arg.startswith('--sysroot=')))
assert (sysroot / 'usr/include/stdio.h').is_file()
mode = 'static' if '-static' in args else 'dynamic'
sys.exit(1 if os.environ.get('TEST_FAIL_PROBE') == mode else 0)
''')
        self.tool(self.tools / "git", "git", '''
if args[0] == 'init':
    Path(args[-1]).mkdir(parents=True)
else:
    dest = Path(args[1])
    if 'fetch' in args:
        (dest / 'head').write_text(args[-1])
    elif 'checkout' in args:
        import shutil
        shutil.copytree(Path(os.environ['TEST_SEED']) / dest.name, dest, dirs_exist_ok=True)
    elif 'rev-parse' in args:
        print((dest / 'head').read_text())
    elif 'show' in args:
        print('1770997568')
''')
        # Bypass binary firmware extraction only; configure/validation still use
        # the production Python code, with the synthetic stock-config checksum.
        self.tool(self.project / "scripts/rtwo-retcn.py", "helper", '''
import retcnlib as r
pins = r.load_pins()
stock = Path(os.environ['TEST_STOCK_CONFIG']).read_bytes()
pins['STOCK_CONFIG_SHA256'] = r.sha256(stock)
if args[0] == 'extract':
    out = Path(args[2]); out.mkdir(); (out / 'kernel.config').write_bytes(stock)
elif args[0] == 'configure':
    Path(args[2]).write_bytes(r.configure(stock, pins))
elif args[0] == 'validate-config':
    try:
        r.validate_config(Path(args[2]).read_bytes(), stock, pins)
    except ValueError as error:
        sys.exit(str(error))
else:
    assert args[0] == 'verify-pins'
''')
        self.tool(self.tools / "make", "make", '''
assert 'USERCFLAGS=' + os.environ['USERCFLAGS'] in args
assert 'USERLDFLAGS=' + os.environ['USERLDFLAGS'] in args
assert 'untrusted-inherited-flags' not in ' '.join(args)
if 'olddefconfig' in args:
    if os.environ.get('TEST_CONFIG_DRIFT'):
        config = Path(next(a.split('=', 1)[1] for a in args if a.startswith('KCONFIG_CONFIG=')))
        config.write_text(config.read_text().replace('CONFIG_CC_CAN_LINK=y', '# CONFIG_CC_CAN_LINK is not set'))
    print('mock olddefconfig complete')
elif 'kernelrelease' in args:
    source = Path(args[args.index('-C') + 1])
    release = os.environ['TEST_KERNEL_VERSION'] + (source / '.scmversion').read_text().strip()
    print(os.environ.get('TEST_KERNEL_RELEASE', release))
else:
    assert 'Image' in args
    sys.exit(77)  # Stop at Image compilation; never synthesize a successful build.
''')
        self.tool(self.tools / "uname", None, "print('Linux' if args == ['-s'] else 'x86_64')\n")
        self.tool(self.tools / "df", None, "print('Filesystem 1024-blocks Used Available Capacity Mounted\\nmock 100000000 0 100000000 0% /')\n")
        for name in ("curl", "pahole", "bc", "bison", "flex", "cpio", "openssl"):
            self.tool(self.tools / name, name, "sys.exit('unexpected external command')\n")

    def tool(self, path, label, body):
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix = f"#!{sys.executable}\nimport json, os, sys\nfrom pathlib import Path\nargs = sys.argv[1:]\n"
        if label:
            prefix += ("with open(os.environ['TEST_LOG'], 'a') as log:\n"
                       f"    log.write(json.dumps([{label!r}, *args]) + '\\n')\n")
        path.write_text(prefix + body)
        path.chmod(0o755)

    def run_build(self, **settings):
        self.attempt += 1
        self.work = self.root / f"work-{self.attempt}"
        self.log.unlink(missing_ok=True)
        return subprocess.run(["bash", str(self.project / "scripts/build-rtwo-retcn.sh")],
                              env={**self.env, "RTWO_RETCN_WORKDIR": str(self.work), **settings},
                              capture_output=True, text=True, timeout=20)

    def calls(self, label):
        return [call[1:] for line in self.log.read_text().splitlines()
                if (call := json.loads(line))[0] == label]

    def test_pinned_userspace_flags_reach_probes_and_both_make_stages(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 77, result.stdout + result.stderr)
        probes = [call for call in self.calls("clang") if call != ["--version"]]
        self.assertEqual(len(probes), 2)
        self.assertNotIn("-static", probes[0])
        self.assertIn("-static", probes[1])
        self.assertIn("static userspace link probe passed", result.stdout)
        make = self.calls("make")
        self.assertEqual(len(make), 3)
        self.assertIn("olddefconfig", make[0])
        self.assertIn("kernelrelease", make[1])
        self.assertIn("Image", make[2])
        self.assertEqual((self.work / "kernel-release.txt").read_text().strip(), self.pins["STOCK_KERNEL_RELEASE"])
        self.assertEqual((self.work / "common/.scmversion").read_text().strip(),
                         self.pins["STOCK_KERNEL_RELEASE"].removeprefix(self.pins["KERNEL_VERSION"]))
        self.assertIn("CONFIG_MODULE_SCMVERSION=y", (self.work / "build/.config").read_text())
        for args in make:
            self.assertIn("USERCFLAGS=--target=aarch64-linux-android31 --sysroot=" +
                          str(self.work / "ndk" / self.pins["NDK_SYSROOT"]) + " -Wno-unused-function", args)
            self.assertIn("USERLDFLAGS=-fuse-ld=lld --rtlib=compiler-rt --target=aarch64-linux-android31", args)
        ndk = [args for args in self.calls("git") if str(self.work / "ndk") in args]
        self.assertTrue(any(args[-1] == self.pins["NDK_REF"] and "fetch" in args for args in ndk))
        sparse = next(args for args in ndk if "sparse-checkout" in args and "set" in args)
        self.assertEqual(sparse[-2:], [self.pins["NDK_SYSROOT"] + suffix
                                      for suffix in ("/usr/include", "/usr/lib/aarch64-linux-android")])

    def test_failed_link_probe_stops_before_configuration(self):
        for mode in ("dynamic", "static"):
            with self.subTest(mode=mode):
                result = self.run_build(TEST_FAIL_PROBE=mode)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(mode + " userspace link probe failed", result.stdout)
                self.assertEqual(self.calls("make"), [])
                self.assertFalse((self.work / "build/.config").exists())
                self.assertIn("userspace link probe failed", (self.work / "userspace-toolchain.log").read_text())

    def test_config_drift_still_fails_and_preserves_diagnostics(self):
        result = self.run_build(TEST_CONFIG_DRIFT="1")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unreviewed stock config drift: CONFIG_CC_CAN_LINK", result.stderr)
        self.assertEqual(len(self.calls("make")), 1)
        self.assertIn("-CONFIG_CC_CAN_LINK=y", (self.work / "config.diff").read_text())
        self.assertIn("mock olddefconfig", (self.work / "configure.log").read_text())
        self.assertTrue((self.work / "build/.config").is_file())
        self.assertEqual((self.work / "stock/kernel.config").read_bytes(), STOCK)
        self.assertFalse((self.project / "out").exists())

    def test_wrong_kernelrelease_stops_before_image_compilation(self):
        result = self.run_build(TEST_KERNEL_RELEASE="5.15.189-wrong-or-duplicate-suffix")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("kernel release mismatch", result.stderr)
        self.assertEqual(len(self.calls("make")), 2)
        self.assertFalse((self.work / "build.log").exists())
        self.assertEqual((self.work / "kernel-release.txt").read_text().strip(),
                         "5.15.189-wrong-or-duplicate-suffix")

    def test_existing_scm_cache_is_not_overwritten(self):
        (self.seed / "common/.scmversion").write_text("unexpected existing version\n")
        result = self.run_build()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("unexpected source .scmversion", result.stderr)
        self.assertEqual(self.calls("make"), [])
        self.assertEqual((self.work / "common/.scmversion").read_text(), "unexpected existing version\n")

    def test_ci_retains_diagnostics_even_before_image_build(self):
        workflow = (r.ROOT / ".github/workflows/build-rtwo-retcn.yml").read_text()
        failure = workflow.split("- name: Preserve build diagnostics on failure", 1)[1]
        self.assertIn("if: failure()", failure)
        self.assertIn("include-hidden-files: true", failure)
        for name in ("build.log", "compiler.txt", "userspace-toolchain.log", "configure.log",
                     "config.diff", "kernel-release.txt", "common/.scmversion", "build/.config", "stock/kernel.config"):
            self.assertIn(".work/rtwo-retcn.*/" + name, failure)
        self.assertNotIn("stock-boot.img", failure)


if __name__ == "__main__":
    unittest.main(verbosity=2)
