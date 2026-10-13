"""
Configuration module for Smart Contract Permission Scanner.
Supports Ethereum Mainnet, Arbitrum One, and Polygon.
"""

import os
from dataclasses import dataclass
from typing import Dict

# Cargar automáticamente variables de entorno desde .env
def _load_env_file(filepath: str = ".env"):
    try:
        from dotenv import load_dotenv
        # load_dotenv walks up parent directories by default
        load_dotenv(filepath)
    except ImportError:
        pass

    # Fallback sin dependencias para leer .env
    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        filepath if os.path.isabs(filepath) else os.path.abspath(filepath),
        os.path.abspath(os.path.join(base_dir, "..", "..", filepath)),
        os.path.join(base_dir, filepath),
    ]

    for target_path in candidates:
        if os.path.exists(target_path):
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v
                break
            except Exception:
                pass

_load_env_file()


def save_env_values(key_values: Dict[str, str], filepath: str = ".env") -> bool:
    """
    Safely updates or appends key-value pairs in the .env file.
    Preserves comments and existing formatting.
    Updates in-memory os.environ automatically.
    """
    from pathlib import Path
    base_dir = Path(__file__).resolve().parent.parent.parent
    target = base_dir / filepath

    existing_lines = []
    if target.exists():
        try:
            with open(target, "r", encoding="utf-8") as f:
                existing_lines = f.readlines()
        except Exception:
            pass

    updated_keys = set()
    new_lines = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k, _ = stripped.split("=", 1)
            k = k.strip()
            if k in key_values:
                val = key_values[k]
                new_lines.append(f'{k}="{val}"\n')
                os.environ[k] = val
                updated_keys.add(k)
                continue
        new_lines.append(line)

    for k, v in key_values.items():
        if k not in updated_keys and v:
            new_lines.append(f'{k}="{v}"\n')
            os.environ[k] = v

    try:
        with open(target, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        return True
    except Exception:
        return False

@dataclass(frozen=True)
class ChainConfig:
    name: str
    chain_id: int
    rpc_url: str
    explorer_api_url: str
    explorer_api_key_env: str
    native_token_symbol: str
    coingecko_id: str
    fallback_api_url: str = ""

CHAINS: Dict[str, ChainConfig] = {
    "ethereum": ChainConfig(
        name="Ethereum Mainnet",
        chain_id=1,
        rpc_url=os.getenv("ETH_RPC_URL", "https://cloudflare-eth.com"),
        explorer_api_url="https://api.etherscan.io/v2/api",
        explorer_api_key_env="ETHERSCAN_API_KEY",
        native_token_symbol="ETH",
        coingecko_id="ethereum",
        fallback_api_url="https://eth.blockscout.com/api",
    ),
    "arbitrum": ChainConfig(
        name="Arbitrum One",
        chain_id=42161,
        rpc_url=os.getenv("ARB_RPC_URL", "https://arb1.arbitrum.io/rpc"),
        explorer_api_url="https://api.etherscan.io/v2/api",
        explorer_api_key_env="ARBISCAN_API_KEY",
        native_token_symbol="ETH",
        coingecko_id="ethereum",
        fallback_api_url="",
    ),
    "polygon": ChainConfig(
        name="Polygon PoS",
        chain_id=137,
        rpc_url=os.getenv("POLYGON_RPC_URL", "https://polygon-rpc.com"),
        explorer_api_url="https://api.etherscan.io/v2/api",
        explorer_api_key_env="POLYGONSCAN_API_KEY",
        native_token_symbol="POL",
        coingecko_id="polygon-ecosystem-token",
        fallback_api_url="",
    ),
    "bsc": ChainConfig(
        name="BNB Smart Chain",
        chain_id=56,
        rpc_url=os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org"),
        explorer_api_url="https://api.etherscan.io/v2/api",
        explorer_api_key_env="BSCSCAN_API_KEY",
        native_token_symbol="BNB",
        coingecko_id="binancecoin",
        fallback_api_url="https://api.bscscan.com/api",
    ),
}

# Sensitive function keywords to flag
SENSITIVE_KEYWORDS = [
    "withdraw",
    "drain",
    "emergencywithdraw",
    "sweep",
    "transferownership",
    "setowner",
    "changeowner",
    "updateparams",
    "setfee",
    "setadmin",
    "grantrole",
    "upgrade",
    "pause",
    "unpause",
    "mint",
    "burn",
]

# Standard access control modifier keywords used in Solidity
ACCESS_MODIFIERS = [
    "onlyowner",
    "onlyrole",
    "onlyadmin",
    "onlygovernance",
    "onlygov",
    "onlyauthorized",
    "auth",
    "restricted",
    "onlykeeper",
    "onlymanager",
]

# Security Intelligence API Configuration (CertiK Skynet)
CERTIK_API_KEY = os.getenv("CERTIK_API_KEY", "")
CERTIK_BASE_URL = os.getenv("CERTIK_BASE_URL", "https://partner.certik-skynet.com")
CERTIK_TIMEOUT_SECONDS = int(os.getenv("CERTIK_TIMEOUT_SECONDS", "10"))

# Default filesystem output directory for generated reports
DEFAULT_OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./reports")

# Slither Execution Configuration (Native & Docker)
SLITHER_BACKEND = os.getenv("SLITHER_BACKEND", "auto")  # 'auto', 'native', 'docker'
SLITHER_DOCKER_IMAGE = os.getenv("SLITHER_DOCKER_IMAGE", "defi-slither:0.10.4")
SLITHER_DEFAULT_TIMEOUT = int(os.getenv("SLITHER_DEFAULT_TIMEOUT", "120"))
