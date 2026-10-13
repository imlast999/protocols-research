"""
Módulo de inteligencia de mercado DeFi para auditoría de contratos inteligentes.
Obtiene y filtra protocolos de alto valor a través de la API pública de DefiLlama
y los conecta con el escáner estático de permisos EVM (contract_permission_scanner.py).
"""

import argparse
import logging
import re
import sys
import time
from typing import Any, Dict, List, Optional
import requests
from web3 import Web3

try:
    from defi_research.github_repository_resolver import normalize_protocol_record
except ImportError:
    from github_repository_resolver import normalize_protocol_record

# Configuración básica de logging
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger("MarketIntelligence")

DEFILLAMA_PROTOCOLS_URL = "https://api.llama.fi/protocols"

CHAIN_ALIAS_MAP = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "polygon": "polygon",
    "matic": "polygon",
    "bsc": "bsc",
    "binance": "bsc",
    "bnb": "bsc",
    "binance smart chain": "bsc",
    "bnb smart chain": "bsc",
}


def clean_contract_address(raw_addr: Optional[str]) -> Optional[str]:
    """
    Limpia y valida que la dirección sea una dirección EVM de 20 bytes válida (40 hex chars).
    Remueve prefijos como 'ethereum:0x...' o espacios.
    """
    if not raw_addr or not isinstance(raw_addr, str):
        return None
    cleaned = raw_addr.strip()
    if ":" in cleaned:
        cleaned = cleaned.split(":")[-1]
    if re.match(r"^0x[a-fA-F0-9]{40}$", cleaned):
        try:
            return Web3.to_checksum_address(cleaned)
        except Exception:
            return None
    return None


def get_high_value_targets(
    min_tvl: float = 50000.0,
    supported_chains: Optional[List[str]] = None,
    timeout: int = 15,
) -> List[Dict[str, Any]]:
    """
    Obtiene la lista de protocolos activos desde DefiLlama y filtra aquellos
    con un TVL superior al mínimo especificado.

    Args:
        min_tvl (float): Valor mínimo de TVL en USD (por defecto: 50,000 USD).
        supported_chains (Optional[List[str]]): Lista opcional de cadenas para filtrar
            (ej. ['ethereum', 'arbitrum', 'polygon']).
        timeout (int): Tiempo de espera en segundos para la petición HTTP.

    Returns:
        List[Dict[str, Any]]: Lista de objetivos de alto valor normalizados.
    """
    try:
        logger.info(f"Consultando protocolos en DefiLlama (min_tvl={min_tvl:,.2f} USD)...")
        response = requests.get(DEFILLAMA_PROTOCOLS_URL, timeout=timeout)
        response.raise_for_status()
        protocols_data = response.json()
    except requests.exceptions.Timeout:
        logger.error(f"Timeout al conectar con DefiLlama ({timeout}s). La API podría estar sobrecargada.")
        return []
    except requests.exceptions.RequestException as req_err:
        logger.error(f"Error de conexión con DefiLlama: {req_err}")
        return []
    except ValueError as json_err:
        logger.error(f"Error al decodificar JSON de DefiLlama: {json_err}")
        return []

    if not isinstance(protocols_data, list):
        logger.warning("Respuesta inválida de DefiLlama (se esperaba lista).")
        return []

    targets: List[Dict[str, Any]] = []

    allowed_chains_lower = (
        {CHAIN_ALIAS_MAP.get(c.lower(), c.lower()) for c in supported_chains}
        if supported_chains
        else None
    )

    for item in protocols_data:
        if not isinstance(item, dict):
            continue

        tvl = item.get("tvl")
        if tvl is None or not isinstance(tvl, (int, float)):
            continue

        if tvl < min_tvl:
            continue

        item_chains = item.get("chains", [])
        primary_chain = item.get("chain", "Unknown")

        # Normalizar nombres de cadenas a minúsculas
        item_chains_norm = [CHAIN_ALIAS_MAP.get(c.lower(), c.lower()) for c in item_chains]
        primary_chain_norm = CHAIN_ALIAS_MAP.get(primary_chain.lower(), primary_chain.lower())

        if allowed_chains_lower:
            has_chain_match = (primary_chain_norm in allowed_chains_lower) or bool(
                set(item_chains_norm) & allowed_chains_lower
            )
            if not has_chain_match:
                continue

        # Extraer dirección EVM válida
        raw_address = item.get("address")
        valid_evm_addr = clean_contract_address(raw_address)
        contracts = [valid_evm_addr] if valid_evm_addr else []

        target_info = {
            "name": item.get("name", "Protocolo Desconocido"),
            "slug": item.get("slug", ""),
            "chain": primary_chain_norm,
            "chains": item_chains_norm,
            "tvl": float(tvl),
            "address": valid_evm_addr,
            "contracts": contracts,
            "category": item.get("category", "General"),
        }

        # Extender con resolución normalizada de GitHub sin romper consumidores existentes
        normalized_target = normalize_protocol_record(item, base_record=target_info)
        targets.append(normalized_target)

    # Ordenar por TVL descendente
    targets.sort(key=lambda x: x["tvl"], reverse=True)
    logger.info(f"Se identificaron {len(targets)} protocolos con TVL >= ${min_tvl:,.2f} USD.")
    return targets


def scan_market_targets(
    min_tvl: float = 50000.0,
    chain: str = "ethereum",
    limit: int = 3,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    CONEXIÓN PIPELINE 1 + 2:
    Usa market_intelligence para obtener objetivos y los inyecta en contract_permission_scanner.
    """
    from contract_permission_scanner import ContractScanner

    chain_key = CHAIN_ALIAS_MAP.get(chain.lower(), chain.lower())
    print("=" * 80)
    print(f"[*] CONEXIÓN: MARKET INTELLIGENCE -> PERMISSION SCANNER")
    print(f"[*] Red: {chain_key.upper()} | Min TVL: ${min_tvl:,.2f} USD | Límite: {limit}")
    print("=" * 80)

    targets = get_high_value_targets(min_tvl=min_tvl, supported_chains=[chain_key])
    scannable = [t for t in targets if t["address"]][:limit]

    if not scannable:
        print("[!] No se encontraron contratos con direcciones EVM directas para escanear.")
        return []

    scanner = ContractScanner(chain_key=chain_key, api_key=api_key)
    results = []

    for idx, proto in enumerate(scannable, 1):
        addr = proto["address"]
        print(f"\n[+] [{idx}/{len(scannable)}] Escaneando {proto['name']} @ {addr}...")
        try:
            report_data = scanner.run_report(addr)
            results.append({
                "protocol": proto["name"],
                "address": addr,
                "chain": chain_key,
                "tvl": proto["tvl"],
                "report": report_data,
            })
            time.sleep(1.0)
        except Exception as e:
            logger.warning(f"Error escaneando {proto['name']} ({addr}): {e}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Market Intelligence & Pipeline Feeder (DefiLlama -> Permission Scanner)"
    )
    parser.add_argument(
        "--min-tvl",
        type=float,
        default=50000.0,
        help="TVL mínimo en USD (default: 50,000)",
    )
    parser.add_argument(
        "--chain",
        default="ethereum",
        choices=["ethereum", "arbitrum", "polygon", "bsc"],
        help="Cadena objetivo (default: ethereum)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Límite de objetivos a listar/escanear (default: 3)",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Ejecutar análisis estático con contract_permission_scanner en los objetivos",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key del explorador de bloques opcional",
    )

    args = parser.parse_args()

    if args.scan:
        scan_market_targets(
            min_tvl=args.min_tvl,
            chain=args.chain,
            limit=args.limit,
            api_key=args.api_key,
        )
    else:
        targets = get_high_value_targets(min_tvl=args.min_tvl, supported_chains=[args.chain])
        print(f"\n[+] Top {min(args.limit, len(targets))} objetivos descubiertos en {args.chain.upper()}:\n")
        for i, t in enumerate(targets[:args.limit], 1):
            addr_str = t["address"] if t["address"] else "(Sin dirección directa)"
            repo_str = t.get("selected_github_url") or f"[{t.get('github_resolution_status')}]"
            print(f"{i}. {t['name']:<25} | TVL: ${t['tvl']:>14,.2f} | Contrato: {addr_str}")
            print(f"   GitHub: {repo_str}")
        print("\nPara escanearlos automáticamente, añade la opción --scan")
