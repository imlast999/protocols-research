# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller Specification File for DeFi Protocol Research Desktop Application.
Packages the PySide6 desktop dashboard into a standalone Windows executable.
Note: PyInstaller does NOT bundle Docker Desktop or the Docker daemon.
Persistent user data is stored safely in %APPDATA%/DeFiResearch.
"""

from pathlib import Path
import sys
from PyInstaller.utils.hooks import copy_metadata

block_cipher = None

root_dir = Path.cwd().resolve()
src_dir = root_dir / "src"

added_files = [
    (str(root_dir / ".env.example"), "."),
    (str(root_dir / "README.md"), "."),
]

# Explicitly collect distribution metadata (.dist-info) for packages that use importlib.metadata.version()
metadata_packages = [
    "py-ecc",
    "web3",
    "eth-account",
    "eth-keyfile",
    "eth-keys",
    "eth-utils",
    "eth-abi",
    "eth-typing",
    "eth-hash",
    "eth-rlp",
    "hexbytes",
    "rlp",
    "requests",
]

for pkg in metadata_packages:
    try:
        added_files.extend(copy_metadata(pkg))
    except Exception as exc:
        print(f"[!] Warning: could not collect metadata for {pkg}: {exc}", file=sys.stderr)

hidden_imports = [
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "requests",
    "web3",
    "py_ecc",
    "eth_account",
    "eth_keyfile",
    "eth_keys",
    "eth_utils",
    "eth_abi",
    "eth_typing",
    "eth_hash",
    "eth_rlp",
    "hexbytes",
    "rlp",
    "dotenv",
    "defi_research",
    "defi_research.config",
    "defi_research.discovery",
    "defi_research.discovery.discovery_store",
    "defi_research.discovery.protocol_hunter",
    "defi_research.gui",
    "defi_research.gui.app",
    "defi_research.gui.main_window",
    "defi_research.gui.theme",
    "defi_research.gui.workers",
    "defi_research.pipeline",
    "defi_research.contract_permission_scanner",
    "defi_research.market_intelligence",
    "defi_research.notrack_script",
    "defi_research.github_repository_resolver",
    "defi_research.slither_runner",
    "defi_research.security_intelligence",
]

a = Analysis(
    [str(root_dir / "desktop_app.py")],
    pathex=[str(root_dir), str(src_dir)],
    binaries=[],
    datas=added_files,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "IPython",
        "jupyter",
        "unittest",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DeFiProtocolResearch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # Windowed GUI application
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DeFiProtocolResearch",
)
