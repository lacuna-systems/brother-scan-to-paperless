#!/usr/bin/env python3
"""Compatibility launcher for brother-scan-to-paperless."""

import os
import sys


source_dir = os.path.dirname(os.path.realpath(__file__))
if source_dir not in sys.path:
    sys.path.insert(0, source_dir)

from brother_scan_to_paperless.cli import main  # noqa: E402


if __name__ == "__main__":
    main()
