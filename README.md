# EVM Smart Contract Audit & Market Intelligence Suite

Suite de auditoría estática de permisos y control de acceso EVM con inteligencia de mercado DeFi en **Ethereum**, **Arbitrum**, **Polygon** y **BNB Smart Chain (BSC)**.

---

## Arquitectura y Conexión de Pipelines

```
   ┌─────────────────────────────────────────────────────────────┐
   │ 1. contract_permission_scanner.py (Motor Base de Auditoría) │
   │    • Descarga ABI y fuente verificada (Etherscan V2 / Bsc)  │
   │    • Análisis estático de signatures y modificadores        │
   │    • Estimación de TVL nativo y comandos de dry-run Foundry │
   └──────────────────────────────┬──────────────────────────────┘
                                  ▲
        Bidireccional             │  Inyección de objetivos
        con --scan-market         │
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │ 2. market_intelligence.py (Inteligencia de Mercado General) │
   │    • API DefiLlama pública (/protocols)                     │
   │    • Filtrado por TVL mínimo y normalización de contratos   │
   │    • Puede alimentar directamente a ContractScanner         │
   └──────────────────────────────┬──────────────────────────────┘
                                  ▲
        Filtro especializado      │  Reutiliza utilidades y escáner
        por sectores de riesgo    │
                                  ▼
   ┌─────────────────────────────────────────────────────────────┐
   │ 3. notrack_script.py (Pipeline Especializado DeFi)          │
   │    • Excluye CEXs y activos pasivos                         │
   │    • Filtra sectores críticos: Lending, Derivatives,        │
   │      Bridges, Liquid Staking, DEXs, Yield                   │
   │    • Resuelve cadena EVM óptima y dispara auditorías        │
   └─────────────────────────────────────────────────────────────┘
```

---

## Instalación

```bash
pip install -r requirements.txt
```

---

## Mini CLI Unificado (`cli.py`)

El proyecto cuenta con un CLI interactivo y estructurado (`cli.py`) con soporte verbose (`-v`) para manejar todo el flujo de forma intuitiva:

### Modo Interactivo (Menú paso a paso)
Si lo ejecutas sin argumentos, se abre el asistente interactivo:
```bash
python cli.py
```

### Comandos del CLI
```bash
# 1. Auditar un contrato específico de forma detallada (verbose):
python cli.py scan -c 0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9 --chain ethereum -v

# 2. Descubrir protocolos en DefiLlama con TVL > 100k y escanearlos:
python cli.py market --chain arbitrum --min-tvl 100000 --limit 3 --scan -v

# 3. Pipeline NoTrack (DeFi crítico: Lending, Bridges, Liquid Staking, DEXs):
# Solo listar candidatos:
python cli.py notrack --chain ethereum --limit 5

# Listar y ejecutar auditoría estática en profundidad en Ethereum o BSC:
python cli.py notrack --chain ethereum --limit 3 --scan -v
python cli.py notrack --chain bsc --limit 3 --scan -v
```

---

## Ejecución por Módulos Individuales

Si prefieres invocar cada archivo directamente:

### 1. `contract_permission_scanner.py`
```bash
# Auditar contrato individual
python contract_permission_scanner.py --contract 0x7Fc665... --chain ethereum

# Auditar objetivos de mercado automáticamente
python contract_permission_scanner.py --scan-market --chain ethereum --limit 3
```

### 2. `market_intelligence.py`
```bash
python market_intelligence.py --chain ethereum --limit 5 --scan
```

### 3. `notrack_script.py`
```bash
python notrack_script.py --chain ethereum --limit 3 --scan
```

### 4. `github_repository_resolver.py` (Phase 1: DefiLlama to GitHub Resolution)
Módulo determinista y configurable para extraer, normalizar y validar repositorios oficiales de GitHub a partir de los metadatos de DefiLlama:

```bash
# Resolver repositorio para un slug de DefiLlama (ej. maple, lido, aave-v3)
python github_repository_resolver.py --slug maple

# Normalizar y clasificar una URL o handle individual
python github_repository_resolver.py --url "https://github.com/aave/aave-v3-core.git"
python github_repository_resolver.py --url "https://github.com/lidofinance"

# Resolver en lote los protocolos de DefiLlama con mayor TVL
python github_repository_resolver.py --limit 10 --min-tvl 100000000

# Usar desde el CLI unificado
python cli.py resolve --slug maple
python cli.py resolve --url "https://github.com/Uniswap/v3-core"
```

#### Estados de Resolución (`ResolutionStatus`):
- `RESOLVED_CONFIDENT`: Repositorio verificado con concordancia directa de organización oficial.
- `RESOLVED_LOW_CONFIDENCE`: Repositorio único obtenido de enlaces de auditoría o metadatos sin organización corroborada.
- `ORGANIZATION_ONLY`: DefiLlama únicamente provee una organización o usuario GitHub (no se inventan nombres de repositorios).
- `AMBIGUOUS`: Múltiples repositorios válidos en conflicto sin un desempate concluyente.
- `UNRESOLVED`: El protocolo no declara ningún enlace de código ni metadatos de GitHub.
- `MALFORMED_RECORD`: El registro de DefiLlama es inválido o corrupto.

### 5. `slither_runner.py` (Phase 2: Slither Static Analysis Integration)
Módulo aislado y seguro para ejecutar análisis estático con Slither sobre repositorios Solidity locales previamente verificados:

```bash
# Ejecutar análisis Slither en un repositorio local verificado (vía CLI)
python cli.py slither -t ./workspace/aave-v3-core --slug aave-v3 --commit e5d4c3b2

# Ejecutar análisis en un repositorio local sin slug de protocolo
python cli.py slither -t ./workspace/my-contracts --commit a1b2c3d4

# Personalizar timeout y directorio de salida de reportes JSON
python cli.py slither -t ./workspace/my-contracts --timeout 180 --output-dir ./reports/slither
```

#### Prerrequisitos de Slither e Instalación
Slither requiere Python 3.8+ y un compilador `solc` compatible disponible en el sistema:
```bash
# 1. Instalar Slither Analyzer
pip install slither-analyzer

# 2. Instalar solc-select para gestionar versiones de Solidity
pip install solc-select
solc-select install 0.8.20
solc-select use 0.8.20
```

#### Interpretación de Resultados, Severidades y Códigos de Salida
- **Estados de Ejecución (`AnalysisStatus`):**
  - `COMPLETED`: Análisis finalizado correctamente. Los hallazgos se estructuran en el reporte JSON.
  - `NOT_ELIGIBLE`: Bloqueado por la puerta de enlace de seguridad (ej. resolución no confiable o directorio fuera del workspace).
  - `TOOL_UNAVAILABLE`: Slither no está instalado o no se encuentra en el `PATH`.
  - `PREREQUISITE_MISSING`: Fallo de verificación previa (sin archivos `.sol`, sin framework de build, o dependencias de compilación no resueltas).
  - `TIMED_OUT`: La ejecución superó el umbral de tiempo configurado.
  - `ANALYSIS_FAILED`: Error interno durante la invocación del subproceso.
- **Códigos de Salida (`Exit Code`):**
  - Slither retorna `0` cuando no encuentra problemas, pero retorna `1-255` cuando detecta vulnerabilidades O cuando falla la compilación. El runner **nunca interpreta un código distinto de cero por sí solo como vulnerabilidad o fallo**, sino que analiza el JSON estructurado.
- **Severidades:** `High` (Crítico/Alto), `Medium` (Medio), `Low` (Bajo), `Informational` (Informativo), `Optimization` (Optimización de gas).
- **Almacenamiento de Reportes:** Los resultados completos se serializan en formato JSON en `./reports/slither/` (ej. `slither_aave-v3_e5d4c3b2_1791836800.json`).

#### Precauciones de Seguridad para Repositorios No Confiables
1. **Subproceso aislado:** Slither se invoca estrictamente con `shell=False`.
2. **Cero ejecución de scripts:** Nunca se ejecutan `npm install`, `forge build`, `npx hardhat` ni scripts de hooks del repositorio. Si faltan dependencias, se reporta `PREREQUISITE_MISSING`.
3. **Aislamiento de Workspace:** Solo se permite analizar directorios dentro de las raíces autorizadas del workspace para prevenir directory traversal.

#### Limitaciones Conocidas
- El análisis estático no puede detectar vulnerabilidades económicas complejas, manipulación de oráculos de precio ni arbitraje por flash loans.
- El código fuente en la rama por defecto no garantiza concordancia con el bytecode desplegado on-chain sin una verificación formal de bytecode contra la blockchain.
- Los resultados de Slither no constituyen una certificación formal de seguridad ni garantía de auditoría completa.

---

### 6. `security_intelligence.py` (Phase 3: CertiK / Skynet Security Intelligence Integration)
Módulo agnóstico de proveedores para consultar telemetría de seguridad externa, puntuaciones de riesgo (Security Scores) y registros de auditorías oficiales (CertiK Skynet):

```bash
# Consultar inteligencia de seguridad para un protocolo por slug (DefiLlama):
python cli.py intelligence --slug aave-v3

# Salida en formato JSON legible por máquina:
python cli.py intelligence --slug aave-v3 --json

# Consultar especificando contrato y red EVM:
python cli.py intelligence --slug aave-v3 --contract 0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9 --chain ethereum
```

#### Configuración de Credenciales
CertiK Skynet opera una API de Partners autenticada (`https://partner.certik-skynet.com/v1/security-scores`). Requiere una clave de API comercial autorizada por CertiK:
- Configura en tu `.env`:
  ```bash
  CERTIK_API_KEY="<tu_certik_partner_api_key>"
  CERTIK_BASE_URL="https://partner.certik-skynet.com"
  CERTIK_TIMEOUT_SECONDS="10"
  ```
- Si la clave no está configurada, el pipeline retorna explícitamente `NOT_CONFIGURED` sin inventar puntajes ni realizar web scraping no autorizado.

#### Reglas de Coincidencia Conservadora (`MatchConfidence`)
Para evitar falsas atribuciones entre proyectos con nombres similares:
1. `EXACT_PROVIDER_ID`: Coincidencia por ID único verificado de CertiK.
2. `CHAIN_CONTRACT_MATCH`: Coincidencia por dirección de contrato en la cadena correspondiente (`bsc:0x...`, `ethereum:0x...`).
3. `OFFICIAL_DOMAIN_MATCH`: Coincidencia por dominio oficial verificado del proyecto.
4. `NAME_SLUG_MATCH`: Coincidencia por nombre o slug únicamente si no existen ambigüedades.
- Si existen múltiples proyectos no diferenciables, se retorna `AMBIGUOUS`.
- **Desacoplamiento Estricto:** La información de inteligencia externa es estrictamente informativa y **nunca puede alterar la elegibilidad de repositorios ni saltarse las puertas de seguridad de Slither**.

---

## Ejecución de Pruebas Automatizadas

El proyecto cuenta con suites de pruebas unitarias 100% deterministas que corren offline sin dependencias externas:

```bash
# Ejecutar todas las suites de prueba (75 tests pasando):
python -m unittest discover -v

# Pruebas del Resolver GitHub (23 tests):
python -m unittest test_github_repository_resolver.py -v

# Pruebas de Slither Runner y Auditoría de Seguridad (31 tests):
python -m unittest test_slither_runner.py -v
python -m unittest test_slither_security_audit.py -v

# Pruebas de Inteligencia de Seguridad CertiK/Skynet (15 tests):
python -m unittest test_security_intelligence.py -v
```


