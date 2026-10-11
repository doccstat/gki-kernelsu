#!/usr/bin/env python3
"""Offline LineageOS packaging/preflight tests; no source sync or compilation."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VARIANT = "resukisu-susfs"


def boot_image(kernel=b"stock kernel", version=4):
    header = bytearray(4096)
    header[:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, len(kernel))
    struct.pack_into("<I", header, 20, 1584)
    struct.pack_into("<I", header, 40, version)
    return (header + kernel).ljust(8192, b"\0")


class BootPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "workspace"
        (self.root / "scripts").mkdir(parents=True)
        shutil.copytree(ROOT / "config/rtwo", self.root / "config/rtwo")
        for name in ("package-rtwo-boot.sh", "build-rtwo.sh", "verify-rtwo-pins.sh"):
            shutil.copy2(ROOT / "scripts" / name, self.root / "scripts" / name)
        self.payload = Path(self.tmp.name) / "download.img"
        self.payload.write_bytes(boot_image())
        self.pins = self.root / "config/rtwo/pins.env"
        self.pin_payload()
        pins = dict(line.split("=", 1) for line in self.pins.read_text().splitlines()
                    if line and not line.startswith("#"))
        self.cache = self.root / ".work/rtwo-base" / pins["LINEAGE_BUILD_DATE"] / "boot.img"
        self.artifacts = self.root / "out/rtwo" / VARIANT
        self.tools = self.root / "tools"
        self.tools.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(("RTWO_", "MKBOOTIMG_", "PACKAGE_BOOT_IMAGE", "VARIANT"))}
        self.env.update({"PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
                         "VARIANT": VARIANT, "MKBOOTIMG_DIR": str(self.tools),
                         "TEST_LOG": str(self.log), "TEST_DOWNLOAD": str(self.payload)})
        self.mock(self.bin / "curl", "curl", '''
output = Path(sys.argv[sys.argv.index('--output') + 1])
status = int(os.environ.get('TEST_CURL_STATUS', '0'))
output.write_bytes(b'partial download' if status else Path(os.environ['TEST_DOWNLOAD']).read_bytes())
sys.exit(status)
''')
        self.mock(self.bin / "uname", None, "print('Linux' if sys.argv[1] == '-s' else 'x86_64')\n")
        self.mock(self.bin / "pahole", None, "pass\n")
        for name in ("git", "make"):
            self.mock(self.bin / name, name, "sys.exit('unexpected source sync or compilation')\n")
        self.mock(self.root / "scripts/fetch-mkbootimg-tools.sh", "tools",
                  "sys.exit(int(os.environ.get('TEST_TOOLS_STATUS', '0')))\n")
        self.mock(self.root / "scripts/prepare-rtwo-source.sh", "source", "sys.exit(77)\n")
        # Minimal tooling mocks exercise shell argument flow and cache handling,
        # not AOSP binary compatibility (the production tooling stays pinned).
        (self.tools / "unpack_bootimg.py").write_text('''
import argparse, os, struct, sys
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--boot_img'); p.add_argument('--out'); p.add_argument('--format')
p.add_argument('--null', action='store_true')
a = p.parse_args()
data = Path(a.boot_img).read_bytes()
assert data[:8] == b'ANDROID!'
size = struct.unpack_from('<I', data, 8)[0]
version = struct.unpack_from('<I', data, 40)[0]
out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
kernel = out / 'kernel'; kernel.write_bytes(data[4096:4096 + size])
if a.format == 'info':
    print('boot image header version:', version)
else:
    sys.stdout.buffer.write(b'--header_version\\0' + str(version).encode() + b'\\0--kernel\\0' + os.fsencode(kernel) + b'\\0')
''')
        (self.tools / "mkbootimg.py").write_text('''
import argparse, struct
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--header_version', type=int); p.add_argument('--kernel'); p.add_argument('--output')
a = p.parse_args()
kernel = Path(a.kernel).read_bytes()
header = bytearray(4096); header[:8] = b'ANDROID!'
struct.pack_into('<I', header, 8, len(kernel))
struct.pack_into('<I', header, 20, 1584)
struct.pack_into('<I', header, 40, a.header_version)
Path(a.output).write_bytes(header + kernel)
''')

    def mock(self, path, label, body):
        prefix = f"#!{sys.executable}\nimport json, os, sys\nfrom pathlib import Path\n"
        if label:
            prefix += ("with open(os.environ['TEST_LOG'], 'a') as stream:\n"
                       f"    stream.write(json.dumps([{label!r}, *sys.argv[1:]]) + '\\n')\n")
        path.write_text(prefix + body)
        path.chmod(0o755)

    def pin_payload(self):
        self.sha = hashlib.sha256(self.payload.read_bytes()).hexdigest()
        text = self.pins.read_text()
        text = "\n".join("LINEAGE_BOOT_SHA256=" + self.sha if line.startswith("LINEAGE_BOOT_SHA256=")
                         else line for line in text.splitlines()) + "\n"
        self.pins.write_text(text)

    def run_script(self, name="package-rtwo-boot.sh", args=("--prepare",), **settings):
        return subprocess.run(["bash", str(self.root / "scripts" / name), *args], cwd=self.root,
                              env={**self.env, **settings}, capture_output=True, text=True, timeout=15)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def labels(self):
        return [call[0] for call in self.calls()]

    def assert_no_partial_cache(self):
        self.assertFalse(self.cache.exists())
        self.assertEqual(list(self.root.glob(".work/rtwo-base/*/boot.img.download.*")), [])
        self.assertEqual(list(self.root.glob(".work/package-rtwo-boot.*")), [])

    def test_prepare_without_kernel_verifies_download_and_tools(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cache.read_bytes(), self.payload.read_bytes())
        self.assertEqual(self.labels(), ["curl", "tools"])
        self.assertFalse(self.artifacts.exists())
        curl = self.calls()[0]
        self.assertIn("--proto", curl)
        self.assertIn("--proto-redir", curl)
        self.assertNotIn("--retry-all-errors", curl)
        self.assertEqual(list(self.cache.parent.glob("*.download.*")), [])

    def test_valid_cache_needs_no_download(self):
        self.cache.parent.mkdir(parents=True)
        shutil.copy2(self.payload, self.cache)
        result = self.run_script(TEST_CURL_STATUS="22")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.labels(), ["tools"])

    def test_corrupt_cache_is_rejected_without_overwriting(self):
        self.cache.parent.mkdir(parents=True)
        self.cache.write_bytes(b"do not overwrite")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertEqual(self.cache.read_bytes(), b"do not overwrite")
        self.assertEqual(self.calls(), [])

    def test_http_failure_leaves_no_cache_and_explains_recovery(self):
        result = self.run_script(TEST_CURL_STATUS="22")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RTWO_BASE_BOOT_IMAGE", result.stderr)
        self.assertIn("RTWO_BASE_BOOT_URL", result.stderr)
        self.assertIn(self.sha, result.stderr)
        self.assertEqual(self.labels(), ["curl"])
        self.assert_no_partial_cache()

    def test_bad_download_hash_never_becomes_cache(self):
        self.payload.write_bytes(b"wrong image")
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertEqual(self.labels(), ["curl"])
        self.assert_no_partial_cache()

    def test_wrong_header_never_becomes_cache(self):
        self.payload.write_bytes(boot_image(version=3))
        self.pin_payload()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a v4", result.stderr)
        self.assert_no_partial_cache()

    def test_missing_local_image_never_downloads_to_supplied_path(self):
        supplied = self.root / "caller owned/missing.img"
        result = self.run_script(RTWO_BASE_BOOT_IMAGE=str(supplied))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing supplied", result.stderr)
        self.assertFalse(supplied.parent.exists())
        self.assertEqual(self.calls(), [])

    def test_local_image_uses_the_pinned_hash_without_downloading(self):
        result = self.run_script(RTWO_BASE_BOOT_IMAGE=str(self.payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.labels(), ["tools"])
        self.assertFalse(self.cache.exists())

    def test_mirror_url_is_used_without_changing_hash(self):
        url = "https://mirror.example/archived-boot.img"
        result = self.run_script(RTWO_BASE_BOOT_URL=url)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0][-1], url)
        self.assertEqual(hashlib.sha256(self.cache.read_bytes()).hexdigest(), self.sha)

    def test_non_https_download_is_rejected(self):
        result = self.run_script(RTWO_BASE_BOOT_URL="http://mirror.example/boot.img")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HTTPS", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_invalid_checksum_is_rejected_before_network(self):
        result = self.run_script(RTWO_BASE_BOOT_SHA256="not-a-checksum")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid base boot SHA-256", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_tool_failure_does_not_promote_download(self):
        result = self.run_script(TEST_TOOLS_STATUS="1")
        self.assertNotEqual(result.returncode, 0)
        self.assert_no_partial_cache()

    def test_packaging_requires_kernel_before_network(self):
        result = self.run_script(args=())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing raw kernel Image", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_packaging_uses_verified_base_and_emits_metadata(self):
        self.artifacts.mkdir(parents=True)
        kernel = b"new kernel payload"
        (self.artifacts / "Image").write_bytes(kernel)
        result = self.run_script(args=())
        self.assertEqual(result.returncode, 0, result.stderr)
        boot = (self.artifacts / "boot.img").read_bytes()
        self.assertEqual(len(boot), len(self.payload.read_bytes()))
        self.assertEqual(boot[4096:4096 + len(kernel)], kernel)
        metadata = (self.artifacts / "boot-metadata.txt").read_text()
        self.assertIn("base_boot_sha256=" + self.sha, metadata)
        self.assertIn("variant=" + VARIANT, metadata)
        self.assertTrue((self.artifacts / "boot.img.sha256").is_file())

    def test_build_packaging_preflight_precedes_source_sync(self):
        result = self.run_script("build-rtwo.sh", (), PACKAGE_BOOT_IMAGE="1")
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertEqual(self.labels(), ["curl", "tools", "source"])

    def test_missing_base_stops_build_before_source_sync(self):
        result = self.run_script("build-rtwo.sh", (), PACKAGE_BOOT_IMAGE="1", TEST_CURL_STATUS="22")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("source", self.labels())
        self.assert_no_partial_cache()

    def test_raw_only_build_does_not_require_base(self):
        result = self.run_script("build-rtwo.sh", (), PACKAGE_BOOT_IMAGE="0", TEST_CURL_STATUS="22")
        self.assertEqual(result.returncode, 77, result.stderr)
        self.assertEqual(self.labels(), ["source"])

    def test_invalid_packaging_option_stops_before_source_sync(self):
        result = self.run_script("build-rtwo.sh", (), PACKAGE_BOOT_IMAGE="invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PACKAGE_BOOT_IMAGE must be 0 or 1", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_unknown_packager_option_has_no_side_effects(self):
        result = self.run_script(args=("--unknown",))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.root / ".work").exists())


class WorkflowTests(unittest.TestCase):
    def test_ci_prepares_base_before_expensive_work(self):
        workflow = (ROOT / ".github/workflows/build-rtwo.yml").read_text()
        prepare = workflow.index("run: ./scripts/package-rtwo-boot.sh --prepare")
        self.assertLess(prepare, workflow.index("- name: Free runner disk for LLVM LTO"))
        self.assertLess(prepare, workflow.index("run: ./scripts/build-rtwo.sh"))
        self.assertIn("PACKAGE_BOOT_IMAGE: 0", workflow)
        self.assertIn("RTWO_BASE_BOOT_URL: ${{ inputs.base_boot_url || vars.RTWO_BASE_BOOT_URL }}", workflow)
        self.assertIn("run: python3 scripts/test-rtwo-boot.py", workflow)

    def test_failed_packaging_retains_only_raw_kernel_artifacts(self):
        workflow = (ROOT / ".github/workflows/build-rtwo.yml").read_text()
        diagnostic = workflow.split("- name: Upload raw kernel if packaging fails\n", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("failure() && steps.kernel.outcome == 'success' && steps.package.outcome == 'failure'", diagnostic)
        self.assertIn("-unpackaged", diagnostic)
        paths = diagnostic.split("          path: |\n", 1)[1].split("          if-no-files-found:", 1)[0]
        self.assertEqual([line.strip() for line in paths.splitlines()],
                         ["out/rtwo/${{ matrix.variant }}/" + name
                          for name in ("Image", "Image.sha256", "build-metadata.txt")])


if __name__ == "__main__":
    unittest.main(verbosity=2)
