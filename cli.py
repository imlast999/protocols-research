#!/usr/bin/env python3
"""
CLI Unificado - Smart Contract Audit & Market Intelligence Suite
Permite interactuar de forma intuitiva, guiada y con modo verbose detallado.
"""

import argparse
import logging
import os
import sys
import time
from typing import Optional

from config import CHAINS
from contract_permission_scanner import ContractScanner
from github_repository_resolver import normalize_github_repo_url, resolve_github_repo_from_slug
from market_intelligence import get_high_value_targets
from notrack_script import audit_vulnerable_targets, get_vulnerable_targets
from security_intelligence import (
    RetrievalStatus,
    check_live_api_connectivity,
    query_protocol_security_intelligence,
)
from slither_runner import AnalysisStatus, run_slither_analysis

# Asegurar codificación utf-8 en consola de Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def setup_logger(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s" if verbose else "[%(levelname)s] %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")
    return logging.getLogger("AuditCLI")


def print_banner():
    banner = r"""
================================================================================
    ___             ___ __     ___ _    ___ 
   /   | __  ______/ (_) /_   /   | |  / (_)
  / /| |/ / / / __  / / __/  / /| | | / / / 
 / ___ / /_/ / /_/ / / /_   / ___ | |/ / /  
/_/  |_\__,_/\__,_/_/\__/  /_/  |_|___/_/   
     EVM Smart Contract Audit & Market Intelligence Suite
================================================================================
    """
    print(banner)


def cmd_scan(args, logger: logging.Logger):
    """Audita un contrato específico directamente."""
    print("=" * 80)
    print(f"[*] COMANDO: AUDITORÍA DE CONTRATO INDIVIDUAL")
    print(f"[*] Contrato: {args.contract}")
    print(f"[*] Cadena:   {args.chain.upper()}")
    print("=" * 80)

    if args.verbose:
        logger.debug(f"Inicializando ContractScanner con RPC: {args.rpc_url or 'default'}")
        logger.debug(f"API Key provista: {'Sí' if args.api_key else 'No (usando fallback/pública)'}")

    try:
        scanner = ContractScanner(
            chain_key=args.chain,
            rpc_url=args.rpc_url,
            api_key=args.api_key,
        )
        t_start = time.time()
        result = scanner.run_report(args.contract)
        duration = time.time() - t_start

        if args.verbose:
            logger.debug(f"Tiempo total de escaneo: {duration:.2f} segundos.")
            logger.debug(f"Total de funciones analizadas: {len(result.get('findings', []))}")
            logger.debug(f"Total funciones críticas: {result.get('critical_count', 0)}")
    except Exception as e:
        logger.error(f"Fallo durante la auditoría de {args.contract}: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()


def cmd_market(args, logger: logging.Logger):
    """Consulta la inteligencia de mercado global de DefiLlama con opción de escaneo."""
    print("=" * 80)
    print(f"[*] COMANDO: INTELIGENCIA DE MERCADO GLOBAL (DefiLlama)")
    print(f"[*] Red:      {args.chain.upper()}")
    print(f"[*] TVL Mín:  ${args.min_tvl:,.2f} USD")
    print(f"[*] Límite:   {args.limit}")
    print("=" * 80)

    if args.verbose:
        logger.debug("Consultando endpoint público de protocolos en DefiLlama...")

    targets = get_high_value_targets(
        min_tvl=args.min_tvl,
        supported_chains=[args.chain],
    )

    if not targets:
        print("[!] No se encontraron protocolos que cumplan con los criterios.")
        return

    print(f"\n[+] Protocolos encontrados: {len(targets)}")
    print("-" * 100)
    print(f"{'#':<3} | {'Nombre':<20} | {'TVL':<16} | {'GitHub Repo':<34} | {'Contrato':<22}")
    print("-" * 100)

    for i, t in enumerate(targets[:args.limit], 1):
        contract_str = (t["address"][:10] + "..." + t["address"][-8:]) if t.get("address") else "(Sin contrato)"
        gh_repo = t.get("selected_github_url")
        gh_status = t.get("github_resolution_status", "N/A")
        gh_display = gh_repo.replace("https://github.com/", "") if gh_repo else f"[{gh_status}]"
        if len(gh_display) > 32:
            gh_display = gh_display[:29] + "..."
        print(f"{i:<3} | {t['name']:<20} | ${t['tvl']:>14,.2f} | {gh_display:<34} | {contract_str:<22}")
    print("-" * 100 + "\n")

    if args.scan:
        print("[*] Iniciando escaneo de contratos descubiertos...")
        scanner = ContractScanner(chain_key=args.chain, api_key=args.api_key)
        for i, t in enumerate(targets[:args.limit], 1):
            if t["address"]:
                print(f"\n>>> [{i}/{min(args.limit, len(targets))}] Auditando {t['name']}...")
                scanner.run_report(t["address"])
            elif args.verbose:
                logger.debug(f"Omitiendo {t['name']} porque no tiene dirección de contrato directa.")


def cmd_notrack(args, logger: logging.Logger):
    """Pipeline especializado en protocolos DeFi críticos (excluyendo CEXs)."""
    print("=" * 80)
    print(f"[*] COMANDO: PIPELINE NOTRACK (DeFi Crítico: Lending, DEX, Bridges)")
    print(f"[*] TVL Mín:  ${args.min_tvl:,.2f} USD")
    print(f"[*] Red:      {args.chain.upper() if args.chain else 'TODAS LAS SOPORTADAS'}")
    print(f"[*] Escaneo:  {'ACTIVADO' if args.scan else 'SOLO DESCUBRIMIENTO'}")
    print("=" * 80)

    if args.scan:
        audit_vulnerable_targets(
            min_tvl=args.min_tvl,
            chain=args.chain,
            limit=args.limit,
            api_key=args.api_key,
        )
    else:
        targets = get_vulnerable_targets(
            min_tvl=args.min_tvl,
            target_chain=args.chain,
        )
        if not targets:
            print("[!] No se encontraron protocolos DeFi con los filtros aplicados.")
            return

        print(f"\n[+] Protocolos identificados: {len(targets)}")
        print("-" * 105)
        print(f"{'#':<3} | {'Nombre':<18} | {'Categoría':<14} | {'Red':<9} | {'TVL':<14} | {'GitHub Repo / Estado':<38}")
        print("-" * 105)
        for i, t in enumerate(targets[:args.limit], 1):
            gh_url = t.get("selected_github_url")
            gh_status = t.get("github_resolution_status", "N/A")
            gh_disp = gh_url if gh_url else f"[{gh_status}]"
            print(f"{i:<3} | {t['name']:<18} | {t['category']:<14} | {t['chain'].upper():<9} | ${t['tvl']:>12,.2f} | {gh_disp:<38}")
            if args.verbose and t.get("main_address"):
                logger.debug(f"    -> Contrato verificado: {t['main_address']}")
        print("-" * 105)
        print("[i] Para escanear estos contratos en profundidad, añade el flag: --scan")


def cmd_resolve(args, logger: logging.Logger):
    """Resuelve el repositorio oficial de GitHub de un protocolo o valida una URL."""
    print("=" * 80)
    print(f"[*] COMANDO: RESOLUCIÓN DE REPOSITORIO GITHUB (DefiLlama / URL)")
    print("=" * 80)

    if args.url:
        print(f"[*] Validando URL individual: {args.url}")
        res = normalize_github_repo_url(args.url)
        print(f"    -> Tipo detectado:     {res.candidate_type.value}")
        print(f"    -> URL normalizada:    {res.normalized_url or '(Ninguna)'}")
        print(f"    -> Propietario / Repo: {res.owner or 'N/A'} / {res.repo or 'N/A'}")
        print(f"    -> Es auditoría:       {res.is_auditor_repo}")
        if res.details:
            print(f"    -> Detalles:           {res.details}")
    elif args.slug:
        print(f"[*] Resolviendo repositorio para slug DefiLlama: {args.slug}")
        rec = resolve_github_repo_from_slug(args.slug)
        print(f"\n[+] Protocolo:            {rec.get('name')} ({rec.get('slug')})")
        print(f"[+] Estado de resolución: {rec.get('github_resolution_status')}")
        print(f"[+] Repo Seleccionado:    {rec.get('selected_github_url') or '(Ninguno)'}")
        evidence = rec.get("resolution_evidence") or {}
        print(f"[+] Nivel de Confianza:   {evidence.get('confidence', 'N/A')}")
        print(f"[+] Razón / Explicación:  {evidence.get('reason', 'N/A')}")
        candidates = rec.get("declared_repository_candidates", [])
        if candidates:
            print(f"\n[*] Candidatos evaluados ({len(candidates)}):")
            for c in candidates:
                c_type = c.get('candidate_type') or c.get('type', 'UNKNOWN')
                c_val = c.get('original_value') or c.get('raw_url', '')
                c_src = c.get('source_field') or c.get('source', '')
                print(f"    - [{c_type}] {c_val} -> {c.get('normalized_url') or '(inválido)'} (fuente: {c_src})")
    else:
        print("[!] Debes especificar --slug <protocol_slug> o --url <github_url>")


def cmd_slither(args, logger: logging.Logger):
    """Ejecuta análisis estático con Slither sobre un repositorio local verificado."""
    print("=" * 80)
    print(f"[*] COMANDO: ANÁLISIS ESTÁTICO SLITHER (Sandboxed Subprocess)")
    print(f"[*] Directorio objetivo: {args.target_dir}")
    if args.slug:
        print(f"[*] Validación de protocolo (DefiLlama slug): {args.slug}")
    print("=" * 80)

    proto_record = None
    if args.slug:
        print(f"[*] Verificando resolución del repositorio para '{args.slug}'...")
        proto_record = resolve_github_repo_from_slug(args.slug)
        status = proto_record.get("github_resolution_status")
        print(f"    -> Estado de resolución GitHub: {status}")

        if status != "RESOLVED_CONFIDENT":
            print(f"\n[!] ACCESO RECHAZADO POR PUERTA DE ENLACE DE SEGURIDAD:")
            print(f"    El protocolo '{args.slug}' tiene estado de resolución '{status}'.")
            print(f"    Motivo: {proto_record.get('resolution_evidence', {}).get('reason')}")
            print(f"    El análisis Slither automatizado sólo está permitido cuando el repositorio")
            print(f"    ha sido resuelto con CONFIDENCIA ALTA (RESOLVED_CONFIDENT).")
            print(f"    Se omite la ejecución de Slither para proteger la integridad del pipeline.")
            return

    print(f"[*] Verificando directorio de fuentes y elegibilidad...")
    res = run_slither_analysis(
        target_dir=args.target_dir,
        protocol_record=proto_record,
        timeout_seconds=args.timeout,
        output_dir=args.output_dir,
        commit_hash=args.commit,
    )

    print(f"\n[+] RESULTADO DEL ANÁLISIS SLITHER:")
    print(f"    -> Estado del análisis:      {res.analysis_status.value}")
    print(f"    -> Protocolo:                 {res.protocol_name}")
    print(f"    -> Commit Git analizado:      {res.commit_hash or '(No determinado)'}")
    print(f"    -> Verificación de fuentes:   {res.source_verification_status}")
    print(f"    -> Versión de Slither:        {res.slither_version or 'N/A'}")
    print(f"    -> Duración:                  {res.duration_seconds:.2f} s")
    print(f"    -> Código de salida:          {res.exit_code if res.exit_code is not None else 'N/A'}")
    print(f"    -> Diagnóstico / Motivo:      {res.reason}")

    if res.analysis_status == AnalysisStatus.COMPLETED:
        summary = res.findings_summary
        print(f"\n[*] Resumen de Hallazgos ({summary.get('total', 0)} total):")
        print(f"    - Crítico / Alto (High):    {summary.get('High', 0)}")
        print(f"    - Medio (Medium):           {summary.get('Medium', 0)}")
        print(f"    - Bajo (Low):               {summary.get('Low', 0)}")
        print(f"    - Informativo (Info):       {summary.get('Informational', 0)}")
        print(f"    - Optimización:             {summary.get('Optimization', 0)}")

        if res.findings:
            print(f"\n[*] Detalle de Hallazgos Principales (hasta 5):")
            for i, f in enumerate(res.findings[:5], 1):
                c_name = f.get("affected_contract") or "N/A"
                print(f"    {i}. [{f.get('severity')}] {f.get('detector_id')} (Contrato: {c_name})")
                print(f"       {f.get('description')[:120]}...")

        if res.raw_artifact_path:
            print(f"\n[+] Reporte completo guardado en: {res.raw_artifact_path}")

    elif res.analysis_status == AnalysisStatus.NOT_ELIGIBLE:
        print(f"\n[!] ANÁLISIS RECHAZADO POR POLÍTICA DE SEGURIDAD:")
        print(f"    {res.reason}")

    elif res.analysis_status == AnalysisStatus.ANALYSIS_FAILED:
        print(f"\n[!] ANÁLISIS FALLIDO:")
        print(f"    {res.reason}")
        if res.stderr_summary:
            print(f"    Detalle del error: {res.stderr_summary[:300]}")

    elif res.analysis_status == AnalysisStatus.TIMED_OUT:
        print(f"\n[!] TIEMPO DE ESPERA AGOTADO:")
        print(f"    {res.reason}")

    elif res.analysis_status == AnalysisStatus.TOOL_UNAVAILABLE:
        print(f"\n[!] INSTRUCCIONES DE INSTALACIÓN DE SLITHER:")
        print(f"    Para habilitar el escáner estático Slither:")
        print(f"    1. Asegúrate de tener Python y pip instalados.")
        print(f"    2. Ejecuta: pip install slither-analyzer")
        print(f"    3. Instala solc-select: pip install solc-select && solc-select install 0.8.20")

    elif res.analysis_status == AnalysisStatus.PREREQUISITE_MISSING:
        print(f"\n[!] PRERREQUISITOS FALTANTES:")
        print(f"    {res.reason}")
        if res.stderr_summary:
            print(f"    Detalle del error del compilador / entorno:")
            print(f"    {res.stderr_summary[:300]}")

    print(f"\n[*] Limitaciones del análisis:")
    for lim in res.limitations[:2]:
        print(f"    - {lim}")


def cmd_intelligence(args, logger: logging.Logger):
    """Consulta inteligencia de seguridad y reputación (CertiK Skynet)."""
    if not getattr(args, "json", False):
        print("=" * 80)
        print(f"[*] COMANDO: INTELIGENCIA DE SEGURIDAD & SKYNET (CertiK Telemetry)")
        if getattr(args, "live_check", False):
            print(f"[*] Modo:               Diagnóstico en vivo (--live-check)")
        if args.slug:
            print(f"[*] Slug DefiLlama:     {args.slug}")
        if args.name:
            print(f"[*] Nombre:             {args.name}")
        if args.contract:
            print(f"[*] Contrato objetivo:  {args.contract}")
        if args.chain:
            print(f"[*] Cadena EVM:         {args.chain.upper()}")
        print("=" * 80)

    if getattr(args, "live_check", False):
        live_res = check_live_api_connectivity()
        if getattr(args, "json", False):
            import json
            print(json.dumps(live_res, indent=2))
            return

        print(f"\n[+] RESULTADO DIAGNÓSTICO EN VIVO (CertiK Partner API):")
        print(f"    -> Proveedor:             {live_res.get('provider')}")
        print(f"    -> Estado:                {live_res.get('status')}")
        print(f"    -> Autenticado:           {live_res.get('authenticated')}")
        if live_res.get("http_status"):
            print(f"    -> Código HTTP:           {live_res.get('http_status')}")
        if live_res.get("diagnostic"):
            print(f"    -> Diagnóstico:           {live_res.get('diagnostic')}")
        return

    proto_name = args.name
    contract_addr = args.contract
    chain = args.chain

    if args.slug and not proto_name:
        try:
            proto_rec = resolve_github_repo_from_slug(args.slug)
            proto_name = proto_rec.get("name") or args.slug
            if not contract_addr and proto_rec.get("address"):
                contract_addr = proto_rec.get("address")
        except Exception as e:
            logger.debug(f"Could not resolve DefiLlama protocol metadata: {e}")

    res = query_protocol_security_intelligence(
        protocol_slug=args.slug,
        protocol_name=proto_name,
        contract_address=contract_addr,
        chain=chain,
    )

    if getattr(args, "json", False):
        print(res.to_json())
        return

    print(f"\n[+] RESULTADO DE INTELIGENCIA DE SEGURIDAD:")
    print(f"    -> Proveedor:             {res.provider_name}")
    print(f"    -> Estado de consulta:    {res.retrieval_status.value}")
    print(f"    -> Nivel de coincidencia: {res.match_confidence.value}")
    print(f"    -> Protocolo:             {res.protocol_name}")
    if res.provider_project_id:
        print(f"    -> ID Proveedor:          {res.provider_project_id}")
    if res.project_url:
        print(f"    -> URL del Proyecto:      {res.project_url}")
    if res.diagnostic:
        print(f"    -> Diagnóstico:           {res.diagnostic}")

    if res.retrieval_status == RetrievalStatus.SUCCESS:
        if res.security_score:
            score_val = f"{res.security_score.score:.1f}/100" if res.security_score.score is not None else "No calificado (N/A)"
            print(f"\n[*] Puntuación de Seguridad Skynet:")
            print(f"    - Score:          {score_val}")
            print(f"    - Tier / Grado:   {res.security_score.tier or 'N/A'}")
            print(f"    - Ranking Global: #{res.security_score.rank or 'N/A'}")
            if res.security_score.sub_scores:
                print(f"    - Sub-métricas:")
                for k, v in res.security_score.sub_scores.items():
                    print(f"        * {k}: {v:.1f}")
        else:
            print(f"\n[*] Puntuación de Seguridad: No disponible o no publicada por el proveedor.")

        if res.audit_records:
            print(f"\n[*] Auditorías Registradas ({len(res.audit_records)}):")
            for i, a in enumerate(res.audit_records, 1):
                d_str = f" ({a.audit_date})" if a.audit_date else ""
                print(f"    {i}. {a.auditor}{d_str} - Estado: {a.status or 'N/A'}")
                if a.report_url:
                    print(f"       Informe: {a.report_url}")
        else:
            print(f"\n[*] Auditorías: No se registran auditorías de este proveedor en el registro consultado.")

    elif res.retrieval_status == RetrievalStatus.NOT_CONFIGURED:
        print(f"\n[!] PROVEEDOR NO CONFIGURADO:")
        print(f"    Para habilitar la API de CertiK Skynet, debes contar con credenciales de Partner.")
        print(f"    Agrega tu clave al archivo .env:")
        print(f"    CERTIK_API_KEY=\"<tu_partner_api_key>\"")

    elif res.retrieval_status == RetrievalStatus.AUTHENTICATION_FAILED:
        print(f"\n[!] FALLO DE AUTENTICACIÓN: La clave CERTIK_API_KEY no es válida.")

    elif res.retrieval_status == RetrievalStatus.RATE_LIMITED:
        print(f"\n[!] LÍMITE DE TASA EXCEDIDO (HTTP 429).")

    print(f"\n[*] Limitaciones & Garantías:")
    for lim in res.limitations[:2]:
        print(f"    - {lim}")


def run_interactive_mode():
    """Modo interactivo con menú paso a paso para uso sencillo."""
    print_banner()
    logger = setup_logger(verbose=False)

    while True:
        print("\n¿Qué acción deseas realizar?")
        print("  [1] Auditar un contrato específico (Dirección + Red)")
        print("  [2] Descubrir protocolos en DefiLlama (Mercado General)")
        print("  [3] Ejecutar Pipeline NoTrack (DeFi Crítico: Lending, Bridges, DEXs)")
        print("  [4] Resolver repositorio GitHub de un protocolo (DefiLlama Slug / URL)")
        print("  [5] Ejecutar análisis estático Slither (Repositorio local verificado)")
        print("  [6] Salir")

        try:
            choice = input("\nSelecciona una opción [1-6]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nSaliendo...")
            sys.exit(0)

        if choice == "1":
            addr = input("Dirección del contrato (0x...): ").strip()
            if not addr.startswith("0x") or len(addr) != 42:
                print("[!] Dirección EVM no válida. Debe empezar por 0x y tener 42 caracteres.")
                continue

            print("Cadenas disponibles: 1. ethereum (default) | 2. arbitrum | 3. polygon | 4. bsc")
            c_input = input("Selecciona cadena [1/2/3/4 o nombre]: ").strip().lower()
            chain_map = {"1": "ethereum", "2": "arbitrum", "3": "polygon", "4": "bsc"}
            chain = chain_map.get(c_input, c_input or "ethereum")

            args = argparse.Namespace(
                contract=addr,
                chain=chain,
                rpc_url=None,
                api_key=None,
                verbose=True,
            )
            cmd_scan(args, logger)

        elif choice == "2":
            print("Cadenas: 1. ethereum | 2. arbitrum | 3. polygon | 4. bsc")
            c_input = input("Cadena [default: ethereum]: ").strip().lower()
            chain = {"1": "ethereum", "2": "arbitrum", "3": "polygon", "4": "bsc"}.get(c_input, c_input or "ethereum")
            tvl_str = input("TVL mínimo en USD [default: 50000]: ").strip()
            min_tvl = float(tvl_str) if tvl_str else 50000.0
            scan_now = input("¿Deseas auditar los contratos descubiertos? (s/N): ").strip().lower() == "s"

            args = argparse.Namespace(
                chain=chain,
                min_tvl=min_tvl,
                limit=3,
                scan=scan_now,
                api_key=None,
                verbose=True,
            )
            cmd_market(args, logger)

        elif choice == "3":
            print("Cadenas: 1. ethereum | 2. arbitrum | 3. polygon | 4. bsc | 5. todas")
            c_input = input("Cadena [default: todas]: ").strip().lower()
            chain = {"1": "ethereum", "2": "arbitrum", "3": "polygon", "4": "bsc"}.get(c_input, None if c_input in ("5", "") else c_input)
            scan_now = input("¿Deseas ejecutar el escaneo de seguridad en profundidad? (S/n): ").strip().lower() != "n"

            args = argparse.Namespace(
                chain=chain,
                min_tvl=5000000.0,
                limit=3,
                scan=scan_now,
                api_key=None,
                verbose=True,
            )
            cmd_notrack(args, logger)

        elif choice == "4":
            slug_input = input("DefiLlama slug del protocolo (ej. aave-v3, uniswap-v3, maple): ").strip()
            if not slug_input:
                print("[!] El slug no puede estar vacío.")
                continue
            args = argparse.Namespace(
                slug=slug_input,
                url=None,
                verbose=True,
            )
            cmd_resolve(args, logger)

        elif choice == "5":
            target_path = input("Ruta al directorio local del repositorio: ").strip()
            if not target_path:
                print("[!] La ruta al directorio no puede estar vacía.")
                continue
            slug_opt = input("Slug DefiLlama (opcional para validación de protocolo, Enter para omitir): ").strip() or None
            commit_opt = input("Commit Git hash (opcional si el repo tiene HEAD, Enter para auto-detectar): ").strip() or None

            args = argparse.Namespace(
                target_dir=target_path,
                slug=slug_opt,
                commit=commit_opt,
                timeout=120,
                output_dir="./reports/slither",
                verbose=True,
            )
            cmd_slither(args, logger)

        elif choice == "6":
            slug_opt = input("Slug DefiLlama (ej. aave-v3, uniswap-v3): ").strip()
            if not slug_opt:
                print("[!] El slug no puede estar vacío.")
                continue
            args = argparse.Namespace(
                slug=slug_opt,
                name=None,
                contract=None,
                chain=None,
                json=False,
                verbose=True,
                live_check=False,
            )
            cmd_intelligence(args, logger)

        elif choice == "7":
            print("Hasta luego.")
            break
        else:
            print("[!] Opción no reconocida. Por favor ingresa un número del 1 al 7.")


def main():
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("-v", "--verbose", action="store_true", help="Activa logs detallados y modo verbose")

    parser = argparse.ArgumentParser(
        prog="cli.py",
        parents=[common_parser],
        description="CLI Unificado - Smart Contract Audit & Market Intelligence Suite",
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Comandos disponibles")

    # Subcomando: scan
    p_scan = subparsers.add_parser("scan", parents=[common_parser], help="Auditar un contrato específico")
    p_scan.add_argument("-c", "--contract", required=True, help="Dirección del contrato (0x...)")
    p_scan.add_argument("--chain", choices=["ethereum", "arbitrum", "polygon", "bsc"], default="ethereum", help="Cadena EVM")
    p_scan.add_argument("--rpc-url", default=None, help="Endpoint RPC personalizado")
    p_scan.add_argument("--api-key", default=None, help="API key del explorador")

    # Subcomando: market
    p_market = subparsers.add_parser("market", parents=[common_parser], help="Inteligencia de mercado general (DefiLlama)")
    p_market.add_argument("--chain", choices=["ethereum", "arbitrum", "polygon", "bsc"], default="ethereum", help="Cadena EVM")
    p_market.add_argument("--min-tvl", type=float, default=50000.0, help="TVL mínimo en USD")
    p_market.add_argument("--limit", type=int, default=3, help="Cantidad de protocolos")
    p_market.add_argument("--scan", action="store_true", help="Escanear los contratos descubiertos")
    p_market.add_argument("--api-key", default=None, help="API key del explorador")

    # Subcomando: notrack
    p_notrack = subparsers.add_parser("notrack", parents=[common_parser], help="Pipeline especializado DeFi (excluyendo CEXs)")
    p_notrack.add_argument("--chain", choices=["ethereum", "arbitrum", "polygon", "bsc"], default=None, help="Cadena EVM específica")
    p_notrack.add_argument("--min-tvl", type=float, default=5000000.0, help="TVL mínimo en USD")
    p_notrack.add_argument("--limit", type=int, default=3, help="Cantidad de protocolos a auditar")
    p_notrack.add_argument("--scan", action="store_true", default=False, help="Ejecutar escaneo de seguridad en profundidad")
    p_notrack.add_argument("--api-key", default=None, help="API key del explorador")

    # Subcomando: resolve
    p_resolve = subparsers.add_parser("resolve", parents=[common_parser], help="Resolver repositorio GitHub de un protocolo o validar URL")
    p_resolve.add_argument("--slug", default=None, help="Slug del protocolo en DefiLlama (ej. aave-v3, uniswap-v3)")
    p_resolve.add_argument("--url", default=None, help="URL de GitHub a validar y normalizar")

    # Subcomando: slither
    p_slither = subparsers.add_parser("slither", parents=[common_parser], help="Ejecutar análisis estático Slither en un repositorio local verificado")
    p_slither.add_argument("-t", "--target-dir", required=True, help="Ruta al directorio local del repositorio")
    p_slither.add_argument("--slug", default=None, help="Slug DefiLlama del protocolo para validar su resolución oficial")
    p_slither.add_argument("--commit", default=None, help="Hash de commit Git específico a auditar")
    p_slither.add_argument("--timeout", type=int, default=120, help="Tiempo límite de ejecución en segundos (default: 120)")
    p_slither.add_argument("--output-dir", default="./reports/slither", help="Directorio para almacenar el reporte JSON")

    # Subcomando: intelligence
    p_intel = subparsers.add_parser("intelligence", parents=[common_parser], help="Consultar inteligencia de seguridad y reputación (CertiK Skynet)")
    p_intel.add_argument("--slug", default=None, help="Slug DefiLlama del protocolo (ej. aave-v3, uniswap-v3)")
    p_intel.add_argument("--name", default=None, help="Nombre del protocolo")
    p_intel.add_argument("--contract", default=None, help="Dirección del contrato inteligente principal")
    p_intel.add_argument("--chain", choices=["ethereum", "arbitrum", "polygon", "bsc"], default=None, help="Cadena EVM")
    p_intel.add_argument("--live-check", action="store_true", help="Diagnóstico seguro opcional de conectividad y autenticación con la API en vivo de CertiK")
    p_intel.add_argument("--json", action="store_true", help="Imprimir salida en formato JSON legible por máquina")

    args = parser.parse_args()

    # Si no se pasa ningún subcomando, lanzar el modo interactivo
    if not args.subcommand:
        run_interactive_mode()
        return

    logger = setup_logger(verbose=args.verbose)

    if args.subcommand == "scan":
        cmd_scan(args, logger)
    elif args.subcommand == "market":
        cmd_market(args, logger)
    elif args.subcommand == "notrack":
        cmd_notrack(args, logger)
    elif args.subcommand == "resolve":
        cmd_resolve(args, logger)
    elif args.subcommand == "slither":
        cmd_slither(args, logger)
    elif args.subcommand == "intelligence":
        cmd_intelligence(args, logger)


if __name__ == "__main__":
    main()

