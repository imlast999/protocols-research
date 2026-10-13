#!/usr/bin/env python3
"""
Smart Contract Permission & Access Control Static Analyzer
Author: Security & Audit Utility
Target: Ethereum, Arbitrum, Polygon

Analyzes verified smart contracts to identify potential access control anomalies
in state-modifying and withdrawal functions using ABI and source code analysis.
"""

import argparse
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

import requests
from web3 import Web3

try:
    from defi_research.config import ACCESS_MODIFIERS, CHAINS, SENSITIVE_KEYWORDS, ChainConfig
except ImportError:
    from config import ACCESS_MODIFIERS, CHAINS, SENSITIVE_KEYWORDS, ChainConfig


class ContractScanner:
    def __init__(self, chain_key: str, rpc_url: Optional[str] = None, api_key: Optional[str] = None):
        if chain_key.lower() not in CHAINS:
            raise ValueError(f"Red '{chain_key}' no soportada. Opciones: {list(CHAINS.keys())}")
        
        self.chain_key = chain_key.lower()
        self.chain_cfg: ChainConfig = CHAINS[self.chain_key]
        
        rpc = rpc_url or self.chain_cfg.rpc_url
        self.w3 = Web3(Web3.HTTPProvider(rpc))
        if not self.w3.is_connected():
            print(f"[!] Advertencia: No se pudo conectar al endpoint RPC: {rpc}")
        
        self.api_key = api_key or os.getenv(self.chain_cfg.explorer_api_key_env, "") or os.getenv("ETHERSCAN_API_KEY", "")

    def get_verified_contract_data(self, contract_address: str) -> Tuple[List[Dict[str, Any]], str, str]:
        """
        Obtiene el ABI y el código fuente verificado desde el explorador correspondiente.
        """
        checksum_addr = Web3.to_checksum_address(contract_address)
        params = {
            "chainid": self.chain_cfg.chain_id,
            "module": "contract",
            "action": "getsourcecode",
            "address": checksum_addr,
        }
        if self.api_key:
            params["apikey"] = self.api_key

        def _fetch_from_endpoint(url: str, query_params: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str, str]:
            resp = requests.get(url, params=query_params, timeout=15)
            data = resp.json()
            if data.get("status") != "1" or not data.get("result"):
                raise RuntimeError(f"{data.get('message', 'Desconocido')} - {data.get('result')}")

            result = data["result"][0]
            contract_name = result.get("ContractName", "UnknownContract")
            raw_abi = result.get("ABI", "[]")
            source_code = result.get("SourceCode", "")

            abi = json.loads(raw_abi)
            return abi, source_code, contract_name

        try:
            return _fetch_from_endpoint(self.chain_cfg.explorer_api_url, params)
        except Exception as primary_exc:
            # Si falla y hay un endpoint de fallback configurado, intentarlo
            if self.chain_cfg.fallback_api_url:
                try:
                    fallback_params = {
                        "module": "contract",
                        "action": "getsourcecode",
                        "address": checksum_addr,
                    }
                    return _fetch_from_endpoint(self.chain_cfg.fallback_api_url, fallback_params)
                except Exception:
                    pass
            raise RuntimeError(f"No se pudo recuperar el ABI/fuente para {contract_address}: {primary_exc}") from primary_exc

    def get_token_price_usd(self) -> float:
        """
        Obtiene el precio aproximado del token nativo en USD via CoinGecko.
        """
        try:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={self.chain_cfg.coingecko_id}&vs_currencies=usd"
            headers = {}
            cg_key = os.getenv("COINGECKO_API_KEY", "").strip()
            if cg_key:
                headers["x-cg-demo-api-key"] = cg_key
            resp = requests.get(url, headers=headers, timeout=5)
            data = resp.json()
            return float(data[self.chain_cfg.coingecko_id]["usd"])
        except Exception:
            return 0.0

    def calculate_approx_tvl(self, contract_address: str) -> Dict[str, Any]:
        """
        Calcula el balance nativo del contrato y su valor estimado en USD.
        """
        checksum_addr = Web3.to_checksum_address(contract_address)
        try:
            native_balance_wei = self.w3.eth.get_balance(checksum_addr)
            native_balance = float(Web3.from_wei(native_balance_wei, "ether"))
        except Exception as e:
            native_balance = 0.0

        price_usd = self.get_token_price_usd()
        usd_value = native_balance * price_usd

        return {
            "native_balance": native_balance,
            "native_symbol": self.chain_cfg.native_token_symbol,
            "native_price_usd": price_usd,
            "approx_tvl_usd": usd_value,
        }

    def _generate_test_param(self, param_type: str, dummy_caller: str) -> Any:
        """
        Genera un valor de prueba no destructivo para simulación estática/dry-run.
        """
        p_type = param_type.lower()
        if "address" in p_type:
            return dummy_caller
        elif "uint" in p_type or "int" in p_type:
            return 0
        elif "bool" in p_type:
            return False
        elif "bytes32" in p_type:
            return "0x0000000000000000000000000000000000000000000000000000000000000000"
        elif "bytes" in p_type:
            return "0x"
        elif "string" in p_type:
            return "audit_test"
        return "0"

    def _check_source_modifiers(self, func_name: str, source_code: str) -> List[str]:
        """
        Examina el código fuente Solidity para identificar modificadores de control de acceso
        declarados en la cabecera de la función.
        """
        if not source_code:
            return []

        # Regex flexible para capturar la firma de la función hasta la apertura del cuerpo '{' o ';'
        pattern = rf"function\s+{re.escape(func_name)}\s*\([^)]*\)\s*([^{{;]+)"
        matches = re.findall(pattern, source_code, flags=re.IGNORECASE | re.DOTALL)
        
        detected_modifiers = []
        for match in matches:
            tokens = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", match)
            for token in tokens:
                if token.lower() in ACCESS_MODIFIERS:
                    detected_modifiers.append(token)
        return list(set(detected_modifiers))

    def analyze_permissions(self, abi: List[Dict[str, Any]], source_code: str) -> List[Dict[str, Any]]:
        """
        Escanea el ABI para detectar funciones de modificación de estado sensibles y
        analiza si cuentan con restricciones evidentes.
        """
        flagged_functions = []
        dummy_address = "0x0000000000000000000000000000000000000001"

        for item in abi:
            if item.get("type") != "function":
                continue

            state_mutability = item.get("stateMutability", "nonpayable")
            # Ignoramos funciones view/pure ya que no modifican estado ni transfieren fondos
            if state_mutability in ("view", "pure"):
                continue

            func_name = item.get("name", "")
            func_name_lower = func_name.lower()
            inputs = item.get("inputs", [])

            # Comprobar si el nombre contiene alguna palabra clave crítica
            is_sensitive = any(kw in func_name_lower for kw in SENSITIVE_KEYWORDS)
            if not is_sensitive:
                continue

            # Revisar si en el código fuente tiene modificadores conocidos
            detected_modifiers = self._check_source_modifiers(func_name, source_code)
            has_guard_modifier = len(detected_modifiers) > 0

            # Categorizar riesgo
            risk_category = "REVISIÓN MANUAL"
            is_critical = False

            if "withdraw" in func_name_lower or "drain" in func_name_lower:
                if not has_guard_modifier:
                    risk_category = "CRÍTICO: Retiro potencialmente sin modificador de acceso"
                    is_critical = True
                else:
                    risk_category = "INFO: Función de retiro protegida por modificador"
            elif "owner" in func_name_lower or "admin" in func_name_lower or "param" in func_name_lower:
                if not has_guard_modifier:
                    risk_category = "ALTO: Modificación administrativa sin modificador explícito"
                    is_critical = True
                else:
                    risk_category = "INFO: Cambio de configuración protegido por modificador"

            # Generar sugerencia de parámetros para dry-run de validación
            suggested_args = [
                {
                    "name": inp.get("name", f"param_{idx}"),
                    "type": inp.get("type", "unknown"),
                    "suggested_value": self._generate_test_param(inp.get("type", ""), dummy_address),
                }
                for idx, inp in enumerate(inputs)
            ]

            arg_types = [inp.get("type", "") for inp in inputs]
            signature = f"{func_name}({','.join(arg_types)})"

            flagged_functions.append({
                "name": func_name,
                "signature": signature,
                "state_mutability": state_mutability,
                "modifiers_found": detected_modifiers,
                "has_guard": has_guard_modifier,
                "risk_category": risk_category,
                "is_critical": is_critical,
                "suggested_inputs": suggested_args,
            })

        return flagged_functions

    def scan_contract(self, contract_address: str) -> Dict[str, Any]:
        """
        Ejecuta el escaneo completo y retorna un diccionario estructurado con los resultados.
        """
        checksum_addr = Web3.to_checksum_address(contract_address)
        abi, source_code, contract_name = self.get_verified_contract_data(checksum_addr)
        tvl_data = self.calculate_approx_tvl(checksum_addr)
        findings = self.analyze_permissions(abi, source_code)

        return {
            "contract_address": checksum_addr,
            "contract_name": contract_name,
            "chain_key": self.chain_key,
            "chain_name": self.chain_cfg.name,
            "chain_id": self.chain_cfg.chain_id,
            "tvl_data": tvl_data,
            "findings": findings,
            "critical_count": sum(1 for f in findings if f.get("is_critical")),
        }

    def run_report(self, contract_address: str) -> Dict[str, Any]:
        """
        Ejecuta el escaneo completo, muestra un informe detallado para auditores
        y retorna los resultados estructurados.
        """
        checksum_addr = Web3.to_checksum_address(contract_address)
        print("=" * 80)
        print(f"[*] INICIANDO AUDITORÍA ESTÁTICA DE PERMISOS")
        print(f"[*] Contrato: {checksum_addr}")
        print(f"[*] Red: {self.chain_cfg.name} (Chain ID: {self.chain_cfg.chain_id})")
        print("=" * 80)

        # 1. Ejecutar escaneo estructurado
        print("[+] Descargando ABI y código fuente verificado...")
        data = self.scan_contract(checksum_addr)
        print(f"[+] Contrato verificado identificado: '{data['contract_name']}'")

        # 2. Resumen TVL
        tvl_data = data["tvl_data"]
        print(f"[+] Balance Nativo: {tvl_data['native_balance']:.4f} {tvl_data['native_symbol']}")
        print(f"[+] TVL Aproximado (Solo Nativo): ${tvl_data['approx_tvl_usd']:,.2f} USD")
        print("-" * 80)

        # 3. Mostrar hallazgos
        findings = data["findings"]
        if not findings:
            print("[+] No se detectaron funciones con firmas sensibles o anomalías inmediatas.")
            return data

        print(f"\n[!] Se identificaron {len(findings)} función(es) candidata(s) para revisión:\n")

        for idx, item in enumerate(findings, 1):
            severity_tag = "[CRÍTICO/ALTO]" if item["is_critical"] else "[INFO/REVISIÓN]"
            print(f"{idx}. {severity_tag} Función: {item['signature']}")
            print(f"   • Tipo: {item['state_mutability']}")
            print(f"   • Diagnóstico: {item['risk_category']}")
            
            if item["modifiers_found"]:
                print(f"   • Modificadores detectados en fuente: {', '.join(item['modifiers_found'])}")
            else:
                print(f"   • Modificadores detectados en fuente: NINGUNO (Posible falta de restricción)")

            # Sugerencia de validación y parámetros
            print(f"   • Sugerencia de Validación (Simulación estática / dry-run):")
            if not item["suggested_inputs"]:
                print(f"     Llamar a `{item['name']}()` sin argumentos desde una dirección sin privilegios.")
            else:
                formatted_args = ", ".join(
                    [f"{p['name']}={p['suggested_value']} ({p['type']})" for p in item["suggested_inputs"]]
                )
                print(f"     Valores de prueba sugeridos: {formatted_args}")

            # Comando cast call de referencia para validación defensiva en local
            dummy_caller = "0x0000000000000000000000000000000000000001"
            cast_args = " ".join([str(p["suggested_value"]) for p in item["suggested_inputs"]])
            print(f"   • Simulación Foundry (solo lectura):")
            print(f"     cast call {checksum_addr} \"{item['signature']}\" {cast_args} --from {dummy_caller} --rpc-url {self.chain_cfg.rpc_url}")
            print("-" * 80)

        print("\n[*] NOTA DE AUDITORÍA:")
        print("  - Los modificadores de Solidity no siempre son capturados por el ABI JSON;")
        print("  - El script cruza la firma con el código fuente para comprobar modificadores;")
        print("  - Cualquier función marcada como CRÍTICA sin modificador debe verificarse")
        print("    en un entorno de simulación local (Foundry / Hardhat fork) para confirmar reverts.")
        print("=" * 80)

        return data


def main():
    parser = argparse.ArgumentParser(
        description="Analizador estático de permisos y firmas en contratos inteligentes (Ethereum, Arbitrum, Polygon)."
    )
    parser.add_argument(
        "-c", "--contract", required=False, default=None, help="Dirección del contrato inteligente (0x...)"
    )
    parser.add_argument(
        "--chain",
        default="ethereum",
        choices=["ethereum", "arbitrum", "polygon", "bsc"],
        help="Cadena objetivo (ethereum, arbitrum, polygon, bsc). Default: ethereum",
    )
    parser.add_argument(
        "--scan-market",
        action="store_true",
        help="Obtener contratos de alto TVL directamente desde market_intelligence.py",
    )
    parser.add_argument(
        "--min-tvl",
        type=float,
        default=50000.0,
        help="TVL mínimo para búsqueda de mercado (default: 50,000 USD)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Cantidad de contratos a escanear en modo mercado (default: 3)",
    )
    parser.add_argument(
        "--rpc-url", default=None, help="Endpoint RPC opcional para Web3"
    )
    parser.add_argument(
        "--api-key", default=None, help="API Key del explorador (Etherscan, Arbiscan, Polygonscan)"
    )

    args = parser.parse_args()

    if not args.contract and not args.scan_market:
        parser.error("Debes especificar una dirección de contrato con --contract o usar el modo mercado con --scan-market.")

    try:
        scanner = ContractScanner(
            chain_key=args.chain,
            rpc_url=args.rpc_url,
            api_key=args.api_key,
        )

        if args.contract:
            scanner.run_report(args.contract)
        elif args.scan_market:
            from market_intelligence import get_high_value_targets

            print(f"[*] Buscando objetivos en {args.chain.upper()} con TVL >= ${args.min_tvl:,.2f} vía market_intelligence...")
            targets = get_high_value_targets(min_tvl=args.min_tvl, supported_chains=[args.chain])
            scannable = [t for t in targets if t.get("address")][: args.limit]

            if not scannable:
                print("[!] No se encontraron contratos con direcciones EVM válidas en market_intelligence.")
                return

            print(f"[+] Iniciando escaneo de {len(scannable)} contratos descubiertos...\n")
            for i, target in enumerate(scannable, 1):
                print(f"\n>>> [{i}/{len(scannable)}] Auditando {target['name']} en {args.chain.upper()}...")
                scanner.run_report(target["address"])

    except Exception as e:
        print(f"[!] Error durante el escaneo: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
