#!/usr/bin/env python3
"""Backward-compatible entrypoint for the maintained lab generator."""

from tools.qradar_lab_loggen import main

if __name__ == "__main__":
    raise SystemExit(main())
