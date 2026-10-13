"""
Regression test for Phase 8.1 packaging metadata resolution and lazy imports.

Verifies:
1. PyInstaller's copy_metadata correctly locates py-ecc and other essential dependencies.
2. defi_research package import is modular/lazy (PEP 562) and does not eagerly import Web3 or py_ecc.
3. Accessing defi_research symbols via lazy loading successfully returns the expected classes/functions.
"""

import sys
import unittest


class TestPackagingMetadata(unittest.TestCase):
    def test_copy_metadata_py_ecc(self):
        """Verify copy_metadata succeeds for py-ecc distribution name."""
        from PyInstaller.utils.hooks import copy_metadata

        datas = copy_metadata("py-ecc")
        self.assertGreater(len(datas), 0, "copy_metadata('py-ecc') returned empty list")
        source_dir, target_dir = datas[0]
        self.assertTrue("py_ecc" in source_dir or "py-ecc" in source_dir)
        self.assertTrue(target_dir.endswith(".dist-info"))

    def test_copy_metadata_all_required_web3_packages(self):
        """Verify all critical web3 packages that might query metadata are collectible."""
        from PyInstaller.utils.hooks import copy_metadata

        required_pkgs = [
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
        ]
        for pkg in required_pkgs:
            datas = copy_metadata(pkg)
            self.assertGreater(len(datas), 0, f"copy_metadata('{pkg}') returned empty list")

    def test_defi_research_lazy_imports(self):
        """Verify importing defi_research does not eagerly pollute sys.modules with web3 or py_ecc."""
        # Check in a fresh subprocess to ensure completely clean sys.modules
        import subprocess

        cmd = [
            sys.executable,
            "-c",
            (
                "import sys\n"
                "import defi_research\n"
                "assert 'web3' not in sys.modules, 'web3 was eagerly imported'\n"
                "assert 'py_ecc' not in sys.modules, 'py_ecc was eagerly imported'\n"
                "cls = defi_research.ContractScanner\n"
                "assert cls is not None\n"
                "assert 'web3' in sys.modules, 'web3 should now be loaded'\n"
            ),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Lazy import test failed: {res.stderr}")


class TestFrozenExecutable(unittest.TestCase):
    """
    Validation tests that specifically exercise the built frozen executable artifact.
    Distinguished from ordinary unit tests: skips cleanly if the executable has not yet been built.
    """

    def setUp(self):
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        self.exe_path = root / "dist" / "DeFiProtocolResearch" / "DeFiProtocolResearch.exe"
        if not self.exe_path.exists():
            self.skipTest(f"Frozen executable not present at {self.exe_path}; run build_exe.py first.")

    def test_frozen_executable_headless(self):
        """Verifies the built executable runs cleanly with --headless flag without metadata crash."""
        import subprocess
        res = subprocess.run(
            [str(self.exe_path), "--headless"],
            capture_output=True,
            text=True,
            timeout=25,
        )
        self.assertEqual(res.returncode, 0, f"Frozen executable failed: stderr={res.stderr}, stdout={res.stdout}")


if __name__ == "__main__":
    unittest.main()

