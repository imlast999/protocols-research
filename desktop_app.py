#!/usr/bin/env python3
"""
Entry point for the DeFi Protocol Research Desktop Application.
Suitable for local execution and PyInstaller bundling.
"""

from pathlib import Path
import sys

# Ensure src/ is on sys.path
_src_dir = str(Path(__file__).resolve().parent / "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

from defi_research.gui.app import main

if __name__ == "__main__":
    main()
