#!/usr/bin/env python3
"""
Pipeline de Auditoría Especializado (notrack_script.py)
Capa de filtrado por categorías DeFi críticas (excluyendo CEXs)
que conecta con market_intelligence.py y contract_permission_scanner.py.
"""

import argparse
import logging
import sys
import time
from typing import Any, Dict, List, Optional
import requests

try:
    from defi_research.config import CHAINS
    from defi_research.contract_permission_scanner import ContractScanner
    from defi_research.market_intelligence import clean_contract_address, CHAIN_ALIAS_MAP
    from defi_research.github_repository_resolver import normalize_protocol_record
except ImportError:
    from config import CHAINS
    from contract_permission_scanner import ContractScanner
    from market_intelligence import clean_contract_address, CHAIN_ALIAS_MAP
    from github_repository_resolver import normalize_protocol_record

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger("NoTrackPipeline")

# Categorías típicas de alto riesgo DeFi (excluyendo CEXs y stablecoins pasivas)
TARGET_CATEGORIES = ["Lending", "Derivatives", "Bridge", "Liquid Staking", "DEX", "Yield"]


def resolve_protocol_chain(proto: Dict[str, Any], forced_chain: Optional[str] = None) -> Optional[str]:
    """
    Identifica la mejor cadena EVM soportada para el protocolo basándose en
    el prefijo de dirección, el desglose de TVL (chainTvls) o las cadenas activas.
    """
    if forced_chain:
        fc = forced_chain.lower()
        return CHAIN_ALIAS_MAP.get(fc, fc)

    # 1. Comprobar si la dirección tiene prefijo explícito (ej. 'arbitrum:0x...')
    raw_addr = str(proto.get("address", ""))
    if ":" in raw_addr:
        prefix = raw_addr.split(":")[0].lower()
        if prefix in CHAIN_ALIAS_MAP:
            return CHAIN_ALIAS_MAP[prefix]

    # 2. Comprobar desglose de TVL por cadenas en DefiLlama
    chain_tvls = proto.get("chainTvls", {})
    candidates = []
    for raw_name, tvl_val in chain_tvls.items():
        c_lower = raw_name.lower()
        if c_lower in CHAIN_ALIAS_MAP and isinstance(tvl_val, (int, float)) and tvl_val > 0:
            candidates.append((CHAIN_ALIAS_MAP[c_lower], tvl_val))

    if candidates:
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    # 3. Comprobar lista general de chains
    for c in proto.get("chains", []):
        c_lower = str(c).lower()
        if c_lower in CHAIN_ALIAS_MAP:
            return CHAIN_ALIAS_MAP[c_lower]

    return None


def get_vulnerable_targets(
    min_tvl: float = 10_000_000,
    target_chain: Optional[str] = None,
    categories: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Obtiene protocolos DeFi relevantes para auditoría, excluyendo CEXs y filtrando
    por sectores con lógica compleja y alto riesgo de contratos.
    """
    url = "https://api.llama.fi/protocols"
    try:
        response = requests.get(url, timeout=12)
        response.raise_for_status()
        protocols = response.json()
    except Exception as e:
        logger.error(f"Error consultando DefiLlama: {e}")
        return []

    active_categories = categories or TARGET_CATEGORIES
    relevant_protocols: List[Dict[str, Any]] = []

    for p in protocols:
        if not isinstance(p, dict):
            continue

        tvl = p.get("tvl") or 0
        if tvl < min_tvl:
            continue

        category = p.get("category", "")
        if category not in active_categories:
            continue

        chain_key = resolve_protocol_chain(p, forced_chain=target_chain)
        if not chain_key or chain_key not in CHAINS:
            continue

        main_addr = clean_contract_address(p.get("address"))

        proto_dict = {
            "name": p.get("name", "Unknown"),
            "slug": p.get("slug", ""),
            "tvl": float(tvl),
            "category": category,
            "chain": chain_key,
            "chains": p.get("chains", []),
            "main_address": main_addr,
            "module": p.get("module", ""),
        }
        normalized_proto = normalize_protocol_record(p, base_record=proto_dict)
        relevant_protocols.append(normalized_proto)

    # Ordenar por TVL descendente para priorizar los mayores objetivos
    return sorted(relevant_protocols, key=lambda x: x["tvl"], reverse=True)


def audit_vulnerable_targets(
    min_tvl: float = 5_000_000,
    chain: Optional[str] = None,
    limit: int = 3,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Flujo Integrado:
    1. Filtra los protocolos más expuestos por categoría y TVL.
    2. Conecta con ContractScanner para auditar permisos, modifiers y TVL nativo.
    3. Muestra un resumen consolidado.
    """
    print("=" * 80)
    print(" PIPELINE NOTRACK: DESCUBRIMIENTO ESPECIALIZADO + ESCANEO DE PERMISOS")
    print("=" * 80)
    print(f"[*] TVL Mínimo: ${min_tvl:,.2f} USD")
    if chain:
        print(f"[*] Cadena seleccionada: {chain.upper()}")
    print(f"[*] Categorías objetivo: {', '.join(TARGET_CATEGORIES[:4])}...")

    targets = get_vulnerable_targets(min_tvl=min_tvl, target_chain=chain)
    print(f"[+] Protocolos identificados: {len(targets)}")

    scannable = [t for t in targets if t["main_address"]][:limit]
    if not scannable:
        print("[!] No se encontraron contratos con dirección EVM directa en la muestra.")
        return []

    print(f"[+] Iniciando auditoría estática en {len(scannable)} contratos prioritarios...\n")

    summary = []
    for idx, proto in enumerate(scannable, 1):
        target_chain_key = proto["chain"]
        target_addr = proto["main_address"]
        print(f"\n>>> [{idx}/{len(scannable)}] {proto['name']} ({target_chain_key.upper()}) @ {target_addr}")

        try:
            scanner = ContractScanner(chain_key=target_chain_key, api_key=api_key)
            report = scanner.run_report(target_addr)
            summary.append({
                "name": proto["name"],
                "category": proto["category"],
                "chain": target_chain_key,
                "tvl": proto["tvl"],
                "critical": report.get("critical_count", 0),
                "status": "Auditado",
            })
            time.sleep(1.0)
        except Exception as e:
            logger.warning(f"Error durante el escaneo de {proto['name']}: {e}")
            summary.append({
                "name": proto["name"],
                "category": proto["category"],
                "chain": target_chain_key,
                "tvl": proto["tvl"],
                "critical": 0,
                "status": f"Fallo ({e})",
            })

    # Tabla resumen
    print("\n" + "=" * 80)
    print(" RESUMEN DE AUDITORÍA (NOTRACK PIPELINE)")
    print("=" * 80)
    print(f"{'Protocolo':<20} | {'Categoría':<15} | {'Red':<9} | {'TVL DefiLlama':<16} | {'Críticos':<8}")
    print("-" * 80)
    for s in summary:
        print(f"{s['name']:<20} | {s['category']:<15} | {s['chain'].upper():<9} | ${s['tvl']:>14,.2f} | {s['critical']:>8}")
    print("=" * 80 + "\n")

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Pipeline NoTrack: Filtrado DeFi de Alto Riesgo + Escaneo de Permisos EVM"
    )
    parser.add_argument(
        "--min-tvl",
        type=float,
        default=5_000_000,
        help="TVL mínimo en USD (default: 5,000,000)",
    )
    parser.add_argument(
        "--chain",
        default=None,
        choices=["ethereum", "arbitrum", "polygon", "bsc"],
        help="Filtrar por cadena específica (ethereum, arbitrum, polygon, bsc)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Número de protocolos a auditar (default: 3)",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        default=False,
        help="Ejecutar el escaneo de contratos automáticamente",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API Key opcional para explorador de bloques",
    )

    args = parser.parse_args()

    if args.scan:
        audit_vulnerable_targets(
            min_tvl=args.min_tvl,
            chain=args.chain,
            limit=args.limit,
            api_key=args.api_key,
        )
    else:
        targets = get_vulnerable_targets(min_tvl=args.min_tvl, target_chain=args.chain)
        print(f"Encontrados {len(targets)} protocolos potenciales.\n")
        for i, proto in enumerate(targets[: args.limit], 1):
            print(f"{i}. {proto['name']} ({proto['category']})")
            print(f"   TVL: ${proto['tvl']:,.2f}")
            print(f"   Red detectada: {proto['chain'].upper()}")
            if proto["main_address"]:
                print(f"   Contract Addr: {proto['main_address']}")
            else:
                print(f"   Module: {proto['module']}")
            repo_info = proto.get("selected_github_url") or f"[{proto.get('github_resolution_status')}]"
            print(f"   GitHub: {repo_info}")
            print("-" * 40)
        print("Para ejecutar la auditoría de permisos completa en estos contratos, usa: python notrack_script.py --scan")