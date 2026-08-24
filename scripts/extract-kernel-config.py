#!/usr/bin/env python3
"""Extract an embedded Linux IKCONFIG from a raw kernel Image."""

from __future__ import annotations

import gzip
import sys
from pathlib import Path


START = b"IKCFG_ST"
END = b"IKCFG_ED"


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} IMAGE", file=sys.stderr)
        return 2

    image = Path(sys.argv[1]).read_bytes()
    offset = 0
    while True:
        start = image.find(START, offset)
        if start < 0:
            break
        end = image.find(END, start + len(START))
        if end < 0:
            break
        payload = image[start + len(START) : end]
        try:
            config = gzip.decompress(payload)
        except OSError:
            offset = start + len(START)
            continue
        if b"CONFIG_" in config and b"Automatically generated" in config:
            sys.stdout.buffer.write(config)
            return 0
        offset = start + len(START)

    print(f"no embedded kernel config found in {sys.argv[1]}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
