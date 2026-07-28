# CentralMind MCP Server

**Code Mode MCP** for enterprise networking APIs — exposing thousands of endpoints across multiple platforms in a highly efficient token footprint.

CentralMind is a fork of [**MistMind**](https://github.com/nagarjun226/mistmind) by [@nagarjun226](https://github.com/nagarjun226). The architecture, sandbox design, and progressive disclosure pattern all come from MistMind — CentralMind extends this capability to support multiple HPE (and related) platforms:

- **HPE Aruba Networking Central** (718+ endpoints)
- **HPE Juniper Mist** (1011+ endpoints)
- **HPE Networking Security Director Cloud (SDC)** (62+ endpoints)
- **HPE Aruba ClearPass** (796+ endpoints)
- **HPE Aruba Networking User Experience Insight (UXI)** (24+ endpoints)
- **HPE Aruba Networking AOS-CX** (switch REST API)
- **Axis Security** (API token)

---

> # ⚠️🚨 READ THIS BEFORE USING `readwrite` MODE 🚨⚠️
>
> **LLMs make mistakes. Period. They will hallucinate endpoints, invent parameters, and confidently execute destructive API calls that look perfectly reasonable.**
>
> When you set `CENTRALMIND_API_MODE=readwrite`, you are giving an AI **unsupervised write access** to your production networking environments. That means it can:
>
> - **Delete SSIDs, VLANs, firewall rules, and certificates**
> - **Push broken configurations to live access points and gateways**
> - **Modify authentication profiles, RADIUS servers, and security policies**
> - **Overwrite port profiles and take down entire switch stacks**
> - **Create, modify, or destroy any resource exposed by the APIs**
>
> The LLM does not understand your network. It does not know which changes are safe. It does not have a rollback button. **It will act with complete confidence while being completely wrong.**
>
> ### 🛑 USE `readwrite` MODE AT YOUR OWN RISK.
>
> **There is no undo. There is no confirmation prompt. There is no safety net.**
>
> If you choose to enable write access, you accept full responsibility for any configuration changes, outages, or damage caused by LLM-generated API calls. **The maintainers of this project are not responsible for your network going down at 3 AM because an AI decided to "optimize" your firewall rules.**
>
> **Default is `readonly` for a reason. Leave it that way unless you genuinely know what you're doing.**

---

## Why CentralMind?

Because [MistMind](https://github.com/nagarjun226/mistmind) works. It solved a real problem: making massive APIs usable by LLMs without blowing up context windows or requiring pre-training. The core insight — give the LLM a tiny index, a sandbox to search the full spec, and a secure way to execute calls — is elegant and generalizable.

CentralMind takes that same approach and applies it across **Central, Mist, SDC, ClearPass, UXI, AOS-CX, and Axis**, which share familiar challenges:

- **Thousands of endpoints** across different products
- **Multiple authentication schemes** (OAuth2 `client_credentials`, API keys, basic auth, etc.)
- **Fragmented OpenAPI specs** that need consolidation and `$ref` resolution

The progressive disclosure pattern from MistMind makes all of this manageable:

- **Initial:** Tiny footprint for the full API hierarchy
- **Search:** LLM writes JS to explore the resolved specs
- **Execute:** LLM chains API calls with full OpenAPI context
- **Enrich (optional):** Heuristics-based post-execute analysis under `_enrichment`

## Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│  Claude Desktop / MCP Client / Agent (skills + tools)       │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  LLM                                                 │  │
│  │  • Sees: search APIs + hierarchy                     │  │
│  │  • Writes: JS to search/execute                      │  │
│  │  • Optional: agent skills under .agents/skills/      │  │
│  └──────────────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────────────┘
                       │ MCP Protocol (stdio)
                       ▼
 ┌─────────────────────────────────────────────────────────────┐
 │  CentralMind MCP Server (Python)                            │
 │  ┌─────────────────┐  ┌──────────────────────────────────┐ │
 │  │  Spec Indexer   │  │  Deno Sandbox                    │ │
 │  │  • Analyzes     │  │  • --deny-net (search mode)      │ │
 │  │    OpenAPI      │  │  • --allow-net=<api hosts>       │ │
 │  │  • Generates    │  │  • Rate limiting (30/min)        │ │
 │  │    hierarchy    │  │  • Token isolation (IIFE)        │ │
 │  │  • Tiny index   │  │  • Output scrubbing              │ │
 │  └─────────────────┘  └──────────────────────────────────┘ │
 │  ┌─────────────────┐  ┌──────────────────────────────────┐ │
 │  │  Auth Managers  │  │  Dynamic Enrichment              │ │
 │  │  • Auto-auth    │  │  • Post-execute heuristics       │ │
 │  │  • Auto-refresh │  │  • Offline / error signals       │ │
 │  └─────────────────┘  └──────────────────────────────────┘ │
 └──────────────┬──────────────────────┬───────────────────────┘
                │                      │
                ▼                      ▼
     spec/*.json (+ auto             Central, Mist, ClearPass,
     *.resolved.json at runtime)     SDC, UXI, AOS-CX, Axis APIs
```

## How It Works

### 1. Authentication (Automatic)
On startup, CentralMind uses your configured credentials (client ID/secret, API tokens, or switch admin credentials) for each platform to authenticate. Tokens are held in memory and auto-refreshed before expiry. No manual token management required.

### 2. Index Generation (Initialization)
Generates lightweight index summaries for each platform containing tags, categories, auth, and pagination info.

### 3. Search (Discovery)
LLM writes JavaScript to explore the spec for a specific platform:

```javascript
async () => {
  const results = [];
  for (const [path, methods] of Object.entries(spec.paths)) {
    if (path.includes('/wlan') && methods.get) {
      results.push({
        method: 'GET',
        path,
        summary: methods.get.summary,
        params: methods.get.parameters
      });
    }
  }
  return results;
}
```

Runs in a hardened Deno sandbox with **no network access** — only reads the local spec file.

### 4. Execute (Action)
LLM chains API calls:

```javascript
async () => {
  // Monitoring endpoint
  const aps = await central.request({ // or mist.request, sdc.request, clearpass.request, …
    path: '/network-monitoring/v1/aps',
    params: { limit: 5 }
  });
  return aps;
}
```

### 5. Dynamic Enrichment (Analysis)
After a successful primary `execute_*` call, if enabled, the server runs a **heuristics-based enrichment pass** and appends a structured `_enrichment` object (operational signals such as offline devices, errors, blast-radius style context, and recommendations). Enrichment is best-effort: if it fails, the original execute result is still returned.

Control with:

| Variable | Default | Meaning |
|----------|---------|---------|
| `CENTRALMIND_ENABLE_ENRICHMENT` | `true` | Turn enrichment on/off |
| `CENTRALMIND_MAX_ENRICHMENT_CALLS` | `3` | Cap on extra JS sandbox calls during enrichment |

### 6. OpenAPI lifecycle (fetch + resolve)
Specs under `spec/` are the source of truth. At serve time, CentralMind **auto-resolves** any `spec/*.json` that is missing a matching `*.resolved.json` (or whose source is newer). Generated `*.resolved.json` files are **gitignored** — they are runtime artifacts, not committed binaries.

Refresh upstream OAS from Aruba’s ReadMe-hosted developer hub:

```bash
# All platforms CentralMind knows how to fetch
python -m centralmind fetch-specs

# Central only (MRT + Config → merged openAPI.json)
python -m centralmind fetch-specs --central-only

# Fetch and run the $ref resolver
python -m centralmind fetch-specs --resolve
```

Legacy flat invocation still works and defaults to serving the MCP:

```bash
python -m centralmind --env-file .env
python -m centralmind serve --env-file .env
```

Optional override for the Central resolved path: `CENTRALMIND_SPEC_PATH`.

## Agent skills (`.agents/skills/`)

Operational **runbooks** for agent clients (Claude, Cursor, Grok, etc.). These are markdown procedures the agent loads when a task matches — not MCP tools registered by the Python server. They encode multi-step workflows on top of CentralMind (and related) tools so the operator does not reinvent the same investigation each time.

| Skill | What it’s for |
|-------|----------------|
| `infrastructure-health-check` | One-shot health across enabled platforms (reachability, alarms, red flags) |
| `morning-coffee-report` | Last-24h digest (engineer or executive tone) for Mist + Central |
| `change-pre-check` / `change-post-check` | Baseline before a change; re-check and verdict after |
| `central-scope-audit` / `central-scope-walker` / `central-scope-visualizer` | Central Configuration Manager scope tree, assignments, and diagrams |
| `central-site-dashboard` | Fast site status board / scorecard for one Central site |
| `central-vlan-configuration` | Create/update VLANs (SVI vs named vs L2) with the right Central APIs |
| `central-qos-policy` | Push switch QoS library objects into Central |
| `central-ucc-quality` | UCC / Wi‑Fi Calling / Teams / Zoom quality on AOS-10 |
| `mist-scope-audit` | Mist org→site config drift (WLANs, RF, templates, variables, …) |
| `clearpass-policy-walker` | Visualize a ClearPass service and its policy decision path |
| `uxi-cross-platform-diagnostics` | Correlate UXI test failures to Central / Mist / AOS 8 |
| `cross-platform-rf-check` | RF / channel / airtime health across a site (multi-platform) |
| `wlan-sync-validation` | Compare Mist vs Central WLAN definitions for drift |
| `aos-migration` | AOS 8 → AOS 10 / Central migration workflow (AOS 6 / IAP out of scope) |
| `greenlake-device-onboarding` | GreenLake device add → subscribe → assign lifecycle |
| `bayesian-inference` | Structured RCA under uncertainty (posteriors over hardware, RF, config, …) |
| `aruba-developer-docs-fallback` | Last-resort docs lookup when MCP tools and platform skills cannot answer |

Browse the full runbooks under [`.agents/skills/`](.agents/skills/).

## Quick Start

### 1. Prerequisites
- Python 3.12+
- [Deno](https://deno.land/) runtime
- API credentials for your desired platforms

### 2. Install
```bash
git clone https://github.com/zemerick1/central-mind.git
cd central-mind
pip install -e .
```

### 3. Configure
```bash
cp .env.example .env
# Edit .env with your credentials
```

### 4. OpenAPI specs
Fetch and/or resolve as needed. On first serve, missing/stale resolved specs are generated automatically when the source JSON is present.

```bash
# Optional: pull latest OAS from Aruba developer hub
python -m centralmind fetch-specs --resolve

# Or resolve locally without fetching
python -m centralmind.spec_resolver spec/openAPI.json spec/openAPI.resolved.json
python -m centralmind.spec_resolver spec/mist.openapi.json spec/mist.resolved.json
# …same pattern for clearpass, sdc, uxi, aoscx, axis, etc.
```

### 5a. Add to Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "centralmind": {
      "command": "python",
      "args": ["-m", "centralmind"],
      "env": {
        "CENTRAL_BASE_URL": "https://<your-cluster>.central.arubanetworks.com",
        "CENTRAL_CLIENT_ID": "your-client-id",
        "CENTRAL_CLIENT_SECRET": "your-client-secret",
        "MIST_APITOKEN": "your-mist-token",
        "SDC_APITOKEN": "your-sdc-token",
        "SDC_HOST": "api.sdcloud.juniperclouds.net",
        "CLEARPASS_BASE_URL": "https://<your-clearpass-url>/api",
        "CLEARPASS_CLIENT_ID": "your-client-id",
        "CLEARPASS_CLIENT_SECRET": "your-client-secret",
        "UXI_CLIENT_ID": "your-client-id",
        "UXI_CLIENT_SECRET": "your-client-secret",
        "CENTRALMIND_API_MODE": "readonly"
      }
    }
  }
}
```

### 5b. Other MCP Clients (Antigravity, Cursor, etc.)

Most MCP-compatible IDEs use a `mcp_config.json` or `mcp_settings.json` file. The format is identical — add a `centralmind` entry to the `mcpServers` object as shown above.

> **Note:** Environment variables set in `env` take priority over a `.env` file. The `.env` file works if the MCP client's working directory is the project root, but most clients don't guarantee that — so setting credentials in the MCP config is the reliable approach.

### 5c. Docker

A `Dockerfile` is included for container runs (Python 3.12 + Deno). Build and run with your preferred compose/stack setup; default entrypoint is `python -m centralmind` (stdio MCP).

## Real-World Examples

### Configuration: Create a static route (HPE Aruba Networking Central)

**Prompt:**
> Configure a static route of 0.0.0.0/0 192.168.63.1 in the library but do not assign it anywhere.

**Output:**
> I have successfully created the default static route (0.0.0.0/0 with the next-hop 192.168.63.1) in the Aruba Central Library.

### Monitoring: Device inventory (HPE Juniper Mist)

**Prompt:**
> How many devices do I have online at the HQ site and what clients are connected?

**Output:**
> You currently have 10 devices online at HQ. Here is the breakdown...

## Security

CentralMind is built with defense-in-depth:

- **Deno sandbox isolation** — Each execution is a fresh process
- **IIFE token closure** — Auth tokens live in closure scope, unreachable by user code
- **stdin token passing** — Tokens never written to disk or source files
- **Network allowlist** — Execute mode only reaches configured API hosts
- **API mode enforcement** — `readonly` blocks all writes (server-side, not bypassable)
- **Rate limiting** — 30 req/min, max 5 concurrent (configurable)
- **Output scrubbing** — Tokens removed from all stdout/stderr/errors
- **In-memory Auth** — Access tokens held in memory only

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `CENTRAL_BASE_URL` | HPE Aruba Networking Central API base URL | |
| `CENTRAL_CLIENT_ID` | HPE Aruba Networking Central OAuth2 client ID | |
| `CENTRAL_CLIENT_SECRET` | HPE Aruba Networking Central OAuth2 client secret | |
| `MIST_APITOKEN` | HPE Juniper Mist API Token | |
| `MIST_HOST` | Mist API host | `api.mist.com` |
| `SDC_APITOKEN` | HPE Networking Security Director Cloud API Key | |
| `SDC_HOST` | SDC API host | `api.sdcloud.juniperclouds.net` |
| `CLEARPASS_BASE_URL`| HPE Aruba ClearPass API base URL | |
| `CLEARPASS_CLIENT_ID`| HPE Aruba ClearPass OAuth2 client ID | |
| `CLEARPASS_CLIENT_SECRET`| HPE Aruba ClearPass OAuth2 client secret | |
| `UXI_CLIENT_ID`| HPE Aruba Networking UXI OAuth2 client ID | |
| `UXI_CLIENT_SECRET`| HPE Aruba Networking UXI OAuth2 client secret | |
| `UXI_HOST`| HPE Aruba Networking UXI API host | `api.capenetworks.com` |
| `UXI_VERIFY_SSL`| Verify SSL certificates for UXI | `true` |
| `AXIS_APITOKEN` | Axis Security API token | |
| `AXIS_HOST` | Axis API host | `admin-api.axissecurity.com` |
| `AOSCX_USERNAME` | AOS-CX administrator username | |
| `AOSCX_PASSWORD` | AOS-CX administrator password | |
| `AOSCX_VERIFY_SSL` | Verify SSL for AOS-CX | `false` |
| `CENTRALMIND_API_MODE` | `readonly` / `readwrite` / `all` | `readonly` |
| `CENTRALMIND_RATE_LIMIT` | Requests per minute (0=unlimited) | `30` |
| `CENTRALMIND_MAX_CONCURRENT`| Max parallel sandbox processes | `5` |
| `CENTRALMIND_SPEC_PATH` | Override path to Central resolved OpenAPI JSON | *(auto)* |
| `CENTRALMIND_ENABLE_ENRICHMENT` | Post-execution enrichment phase | `true` |
| `CENTRALMIND_MAX_ENRICHMENT_CALLS` | Max extra JS calls during enrichment | `3` |

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v --cov     # Run tests with coverage
ruff check src/ tests/               # Lint
ruff format src/ tests/              # Format

# CLI smoke
python -m centralmind --help
python -m centralmind fetch-specs --help
```

## Project Structure

```text
central-mind/
├── src/centralmind/       # Source code
│   ├── __main__.py        # CLI (serve / fetch-specs)
│   ├── auth.py            # OAuth2 / token / basic auth
│   ├── config.py          # Pydantic settings
│   ├── sandbox.py         # Deno sandbox (search + execute)
│   ├── server.py          # MCP server + dynamic enrichment
│   ├── spec_fetcher.py    # Pull OAS from Aruba developer hub
│   ├── spec_indexer.py    # OpenAPI → tiny token index
│   └── spec_resolver.py   # $ref resolver
├── tests/                 # Unit / enrichment / fetcher tests
├── spec/                  # Source OpenAPI JSON (resolved files generated, gitignored)
├── .agents/skills/        # Agent runbooks (markdown skills)
├── Dockerfile             # Container image (Python + Deno)
├── pyproject.toml
└── README.md
```

## License

MIT
