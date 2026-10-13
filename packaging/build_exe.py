#!/usr/bin/env python3
"""
Reproducible Windows Executable Packaging Script using PyInstaller.

Builds the standalone DeFi Protocol Research Desktop Application.
Output location: dist/DeFiProtocolResearch/DeFiProtocolResearch.exe
"""

from pathlib import Path
import subprocess
import sys


def build_executable():
    root_dir = Path(__file__).resolve().parent.parent
    spec_path = root_dir / "packaging" / "defi_research.spec"

    print("=" * 80)
    print(" BUILDING WINDOWS DESKTOP EXECUTABLE (PyInstaller)")
    print("=" * 80)
    print(f"[*] Workspace Root: {root_dir}")
    print(f"[*] Spec File:      {spec_path}")
    print(f"[*] Output Target:  {root_dir / 'dist' / 'DeFiProtocolResearch'}")
    print("=" * 80)

    try:
        import PyInstaller
        print(f"[+] PyInstaller version {PyInstaller.__version__} detected.")
    except ImportError:
        print("[!] PyInstaller is not installed in the current environment.")
        print("[!] Install via: pip install pyinstaller")
        sys.exit(1)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        str(spec_path),
    ]

    print(f"[*] Executing command: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=str(root_dir))

    if result.returncode == 0:
        exe_path = root_dir / "dist" / "DeFiProtocolResearch" / "DeFiProtocolResearch.exe"
        print("\n" + "=" * 80)
        print(" [SUCCESS] EXECUTABLE PACKAGING COMPLETED")
        print("=" * 80)
        print(f"[+] Binary location: {exe_path}")
        print("[+] User data path:  %APPDATA%\\DeFiResearch\\discovery")
        print("\n[!] Important Notice:")
        print("    - PyInstaller packages Python and PySide6, but does NOT include Docker Desktop.")
        print("    - Protocol discovery, repo resolution, and CertiK queries work out-of-the-box.")
        print("    - To run live Slither static analysis, Docker Desktop must be running on Windows.")
        print("=" * 80)
    else:
        print(f"\n[!] PyInstaller build failed with exit code {result.returncode}.")
        sys.exit(result.returncode)


if __name__ == "__main__":
    build_executable()
