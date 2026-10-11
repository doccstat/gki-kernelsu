# Pixel `yogi` and Motorola `rtwo` kernel workspace

Pinned source-build wrappers for Pixel `yogi` / GS101, Motorola `rtwo`
LineageOS, and stock RTWO RETCN. This is not a prebuilt-kernel mirror.

All build targets live on **`main`**. LineageOS and RETCN are separate build
paths, not Git branches. Choose the matching workflow on `main`:

| Target | Workflow | Local build entry point |
| --- | --- | --- |
| Pixel yogi | Build yogi GKI kernel | `scripts/build.sh` |
| RTWO LineageOS | Build rtwo LineageOS GKI KernelSU variants | `scripts/build-rtwo.sh` |
| RTWO stock RETCN | Build rtwo RETCN ReSukiSU SUSFS | `scripts/build-rtwo-retcn.sh` |

Each workflow also responds to relevant `main` pushes; RETCN needs the stock
boot-image input described below before compilation can run.

The Pixel build keeps Google's common GKI and Google module source trees together.
The resulting build includes Google's generated GKI `boot.img`; the stock
`yogi` vendor modules remain from the matching factory image.

## Important feature-matrix decision

The current upstream implementations do not provide one clean, pinned kernel
tree that contains both SukiSU-Ultra KPM and the ReSukiSU/SUSFS kernel hooks:

| Variant | Root implementation | KPM | SUSFS | Purpose |
| --- | --- | ---: | ---: | --- |
| `sukisu-kpm` | SukiSU-Ultra `v4.1.3` | yes | no | Primary KPM build |
| `resukisu-susfs` | ReSukiSU pinned commit | no | yes | SUSFS comparison/fallback |

This project fails closed instead of silently claiming that an untested
KPM+SUSFS combination works. Adding a combined variant requires a reviewed
bridge patch between SukiSU-Ultra's KPM implementation and ReSukiSU's SUSFS
integration. The Google source, lockfile, and build machinery are shared by
both variants.

KPM is not required for normal KernelSU root or SUSFS operation. It is enabled
here because you specifically want to experiment with runtime KernelPatch
modules. Treat the KPM image as the higher-risk build and start with no third-
party KPM payloads installed.

## Pixel pins

The source pins are in [`config/pins.env`](config/pins.env). Google projects
are locked by commit in [`config/manifest-lock.xml`](config/manifest-lock.xml),
including the GS101 Google driver/module repositories. The common kernel is
the exact Android common `6.12.89` commit used for the current factory-image
baseline (`google/yogi/yogi:17/CD1A.260618.001.A9/15850191`, security patch
`2026-06-05`). The Google module SHAs are an immutable snapshot of the GS101
production branch captured in `GOOGLE_MODULES_LOCK_CAPTURED`; verify that
snapshot against the factory image's vendor module release before flashing.
Update the common commit and module commits together when moving to another
factory image.

For the pinned SukiSU-Ultra KPM commit, the build applies a small local
compatibility adjustment for Linux 6.12's `netlink_kernel_cfg` layout
(`cb_mutex` was removed and `release` was added). The patch is applied only to
the Yogi source preparation step and is recorded in the generated source
modifications.

## Motorola Edge+ 2023 (`rtwo`)

### LineageOS build target

The existing `build-rtwo.sh` recipe and its pins remain LineageOS-only. Do not
use its `boot.img` as a stock RETCN replacement.

The workspace builds the LineageOS GKI Image for the Motorola Edge+ 2023.
Its current baseline is recorded in
[`config/rtwo/pins.env`](config/rtwo/pins.env): Android 13/5.15 GKI,
Kalama, kernel `5.15.208-ge3f43b79f663`, and the matching W1TR36H.56-13
Lineage/vendor module source. The build keeps the stock `vendor_dlkm` and
`system_dlkm` modules; it replaces only the common boot `Image`.

The two rtwo variants deliberately match the Pixel matrix:

| Variant | Root implementation | KPM | SUSFS |
| --- | --- | ---: | ---: |
| `sukisu-kpm` | SukiSU-Ultra | yes | no |
| `resukisu-susfs` | ReSukiSU | no | yes |

Build on Linux x86-64, or dispatch the rtwo workflow from GitHub Actions:

```sh
VARIANT=sukisu-kpm ./scripts/build-rtwo.sh
VARIANT=resukisu-susfs ./scripts/build-rtwo.sh
```

The GitHub Actions workflow verifies the base boot image and packaging tools
**before compilation**, then builds the raw `Image` and packages `boot.img` in
separate steps. The packager checks the base SHA-256 and v4 header, replaces only
the common kernel payload, and validates the result with pinned AOSP `mkbootimg`
tools. Stock `vendor_boot`, `vendor_dlkm`, and `system_dlkm` remain untouched.

**Old LineageOS downloads can expire.** The pinned `20260822` download currently
returns HTTP 404. Supply a credential-free HTTPS mirror of the **same original
boot image** through workflow input `base_boot_url` or repository variable
`RTWO_BASE_BOOT_URL`. The expected SHA-256 remains
`19e22bfd013f6cc934c5ee667084cc678b8601237a8dfd346c031fbf1e708f08`;
changing the URL does not change the baseline or disable verification. Without
a matching local copy or working mirror, packaged builds remain blocked rather
than silently using a newer LineageOS, RETCN, or already-patched boot image.

For a complete local build with a saved base:

```sh
VARIANT=resukisu-susfs PACKAGE_BOOT_IMAGE=1 \
  RTWO_BASE_BOOT_IMAGE=/path/to/lineage-20260822/boot.img ./scripts/build-rtwo.sh
```

This also verifies the base before source sync. A raw-only local build (the
default commands above) does not need the base image. If a later CI packaging
step fails after compilation, an `-unpackaged` artifact retains only the raw
kernel and build metadata, not a potentially incomplete boot image. A raw
`Image` is **not** a flashable Android `boot.img`. To package a retained Image
without recompiling:

```sh
VARIANT=resukisu-susfs RTWO_IMAGE=/path/to/Image \
  RTWO_BASE_BOOT_IMAGE=/path/to/lineage-20260822/boot.img ./scripts/package-rtwo-boot.sh
```

The base/tooling pins are in [`config/rtwo/pins.env`](config/rtwo/pins.env).
Keep the generated image paired with the matching W1TR36H.56-13 Lineage/vendor
set; do not mix it with a slot whose kernel release, vendor modules, or KMI
differs from the recorded baseline. Retain a known-good recovery image.

Validate pins/tests offline, or check packaging inputs without building:

```sh
./scripts/verify-rtwo-pins.sh
python3 scripts/test-rtwo-boot.py
RTWO_BASE_BOOT_IMAGE=/path/to/lineage-20260822/boot.img ./scripts/package-rtwo-boot.sh --prepare
```

### Stock RETCN build target

The separate [`config/rtwo-retcn/pins.env`](config/rtwo-retcn/pins.env) targets
**Android 16 `W1TR36H.1-33`**, whose kernel is still **Android 13 / 5.15**:
`5.15.189-android13-8-00018-g9c82b71884fd-ab14885418`. It pins the exact public
Android common commit, clang **r450784e / 14.0.7**, and paired ReSukiSU/SUSFS
commits. Only **`resukisu-susfs`** is implemented for RETCN; no KPM or extra
WildKernels performance/baseband patches are added.

Use a case-sensitive Linux x86-64 builder with at least 35 GiB free (100 GiB
recommended), enough RAM/swap for full LTO, and the dependencies listed in
[the RETCN workflow](.github/workflows/build-rtwo-retcn.yml). Provide the
**original extracted RETCN `boot.img`**, not a BOOST, LineageOS, or already
patched image:

```sh
python3 scripts/rtwo-retcn.py verify-pins
python3 scripts/test-rtwo-retcn.py
RTWO_RETCN_BOOT_IMAGE=/path/to/W1TR36H.1-33/boot.img ./scripts/build-rtwo-retcn.sh
```

Its SHA-256 must be
`993a72b3da6fb47f7548ad6dc3a4c2e99e14e6bd06c81ba11b322f29bd0c6e21`.
There is no checksum override or Lineage fallback. The stock Image and embedded
config are independently checked too; no phone connection is needed.

For GitHub Actions, select **Build rtwo RETCN ReSukiSU SUSFS** on `main` and
supply `stock_boot_url`, an HTTPS URL serving that exact boot file. Alternatively
set the repository variable `RTWO_RETCN_BOOT_URL` to enable RETCN builds on
relevant `main` pushes. Without the variable, pushes run offline tests only. Firmware is not
redistributed in this repository; use a credential-free download URL because
workflow inputs/environment may appear in logs.

Output is under `out/rtwo-retcn/resukisu-susfs/`:

- **`boot-retcn-susfs.img`**: complete boot-v4 image, not an AnyKernel3 ZIP.
- Raw `Image`, embedded final `kernel.config`, stock config diff,
  `vmlinux.symvers`, source pins/patch, build log and checksums.

The build starts with stock IKCONFIG, retaining full LTO, CFI, 4 KiB pages,
MODVERSIONS, vendor hooks and KABI reserves. It adds built-in root/SUSFS,
disables export trimming and GKI protected-export enforcement so custom-key
policy does not reject stock GKI modules, but does **not** disable CRC checks,
enable forced module loading, or force signature enforcement. Unexpected
stock config drift fails the build. The stock kernel release is retained for
module-directory compatibility; this is a modified kernel, not an OEM build.

The packager preserves the stock header except for the replacement kernel's
size and recreates zero partition padding. It deliberately **removes stale OEM
AVB metadata/footer** rather than pretending their signature covers the new
kernel. The result is unsigned and requires an unlocked bootloader. It does
not modify `init_boot`, `vendor_boot`, `dtbo`, `vbmeta`, modules or userdata.
No LKM/AnyKernel3 installation is needed first; install the matching ReSukiSU
manager afterward. Never relock with this image installed.

**Experimental:** patch applicability and offline packaging tests do not prove
stock-module ABI or hardware compatibility. The first full build and boot/module
validation are still required. Keep the original matching RETCN boot image for
recovery; temporary-boot the custom image first if the bootloader supports it.
Neither the build nor packaging scripts query, flash, wipe, change slots, or
reboot a device.

Each build uses a new `.work/rtwo-retcn.*` directory. Existing artifacts are
never overwritten: move reviewed output aside or choose new
`RTWO_RETCN_ARTIFACT_DIR` and (optionally) `RTWO_RETCN_WORKDIR` paths for a
rerun. Failed source/build directories are retained for diagnosis.

## Pixel build on Linux

The source can be inspected/synchronised on macOS, but the Google Kleaf build
needs Linux x86-64 and Google's Linux toolchain prebuilts. A GitHub Actions
workflow is included for this reason.

Local Linux build:

```sh
VARIANT=sukisu-kpm ./scripts/build.sh
# or:
VARIANT=resukisu-susfs ./scripts/build.sh
```

The first sync needs substantial disk space (plan for at least 100 GB) and a
large amount of network traffic. The build writes only under `.work/` and
`out/`, both ignored by Git. The yogi artifact contains Google's generated
`boot.img`, the raw `Image`, the embedded final `kernel.config`, hashes, and
metadata. The build rejects images whose final Kleaf configuration does not
match the requested root variant or leaves Google's protected-export policy
enabled. Yogi keeps the factory `vendor_dlkm` modules, which are not signed
with the custom kernel's locally generated key; clearing only
`MODULE_SIG_PROTECT_LIST` is therefore intentional and required for those
stock modules to load. The generated boot image still needs the matching stock
`vendor_boot`, `vendor_dlkm`,
`system_dlkm`, and verified-boot setup from the same factory baseline; it is
not interchangeable with another build.

GitHub Actions builds both variants on every push and on manual dispatch from
`.github/workflows/build.yml`. The workflow pins the repo launcher checksum and
all source commits; the runner image and GitHub
Actions themselves are still external infrastructure and should be audited
when long-term bit-for-bit reproducibility matters.

## Updating for a new factory image

1. Record the new factory build fingerprint, security patch level, active slot,
   and `uname -r`.
2. Select the matching Android common kernel tag/commit and the matching
   `gs-android16-6.12-gs101` Google module commits.
3. Update `config/pins.env`, then run
   `python3 scripts/lock-manifest.py` and review
   `config/manifest-lock.xml`; do not update only the common kernel while
   retaining unrelated module commits.
4. Build and boot-test the generated `boot.img` using the stock image's
   `vendor_boot` and other matching partitions. Keep the untouched factory
   image and both A/B slots available for recovery.

Disabling OTA does not remove the A/B compatibility problem: an OTA can place
a new boot/vendor set in the inactive slot. Factory-image updates are a good
controlled workflow, but every update still needs a new source lock and a
matching kernel artifact before switching slots.
