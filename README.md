# Pixel `yogi` GKI kernel workspace

This is a reproducible build wrapper for the Pixel `yogi` / GS101 kernel
source published by Google. It is intentionally a source-build workspace, not
a prebuilt-kernel mirror.

The build keeps Google's common GKI and Google module source trees together.
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

## Pins

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

The same workspace also builds the LineageOS GKI Image for the connected
Motorola Edge+ 2023. Its current baseline is recorded in
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

The GitHub Actions workflow sets `PACKAGE_BOOT_IMAGE=1`, so each successful
variant now contains both the raw `Image` and a generated `boot.img`. The
packager downloads the standalone `boot.img` from the pinned LineageOS build,
verifies its SHA-256, replaces only its common kernel payload, preserves the
Android boot header, and validates the result after rebuilding it with the
pinned AOSP `mkbootimg` tools. The stock `vendor_boot`, `vendor_dlkm`, and
`system_dlkm` images are not changed and are not included in this artifact.

To produce the same packaged image in a Linux build, set
`PACKAGE_BOOT_IMAGE=1`. The base boot image and tooling pins are in
[`config/rtwo/pins.env`](config/rtwo/pins.env). Keep the generated image paired
with the matching W1TR36H.56-13 Lineage/vendor set; do not mix it with an OTA or
factory-image slot whose kernel release, vendor modules, or KMI differ from the
recorded baseline. Retain an untouched bootable slot for recovery.

Run the static lock validation before building:

```sh
./scripts/verify-rtwo-pins.sh
```

## Build on Linux

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
`boot.img` as well as the raw `Image`, hashes, and metadata. The generated boot
image still needs the matching stock `vendor_boot`, `vendor_dlkm`,
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
