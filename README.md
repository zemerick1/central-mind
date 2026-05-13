# CentralMind MCP Server

**Code Mode MCP** for enterprise networking APIs — exposing thousands of endpoints across multiple platforms in a highly efficient token footprint.

CentralMind is a fork of [**MistMind**](https://github.com/nagarjun226/mistmind) by [@nagarjun226](https://github.com/nagarjun226). The architecture, sandbox design, and progressive disclosure pattern all come from MistMind — CentralMind extends this capability to support multiple HPE platforms:

- **HPE Aruba Networking Central** (718+ endpoints)
- **HPE Juniper Mist** (1011+ endpoints)
- **HPE Networking Security Director Cloud (SDC)** (62+ endpoints)
- **HPE Aruba Clearpass** (796+ endpoints)
- **HPE Aruba Networking User Experience Insight (UXI)** (24+ endpoints)
- **HPE Aruba Networking AOS-CX Switches** (672+ endpoints)
  - *Note: AOS-CX supports dynamic endpoint generation. The Username and Password must be the same for every switch in the `.env` file.*

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

CentralMind takes that same approach and applies it to **HPE Aruba Networking Central, HPE Juniper Mist, HPE Networking Security Director Cloud, HPE Aruba Clearpass, and HPE Aruba Networking UXI**, which have their own challenges:
- **Thousands of endpoints** across different products
- **Multiple Authentication schemes** (OAuth2 `client_credentials`, API keys, etc.)
- **Fragmented specs** that needed consolidation

The progressive disclosure pattern from MistMind makes all of this manageable:
- **Initial:** Tiny footprint for the full API hierarchy
- **Search:** LLM writes JS to explore the resolved specs
- **Execute:** LLM chains API calls with full OpenAPI context

## Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│  Claude Desktop / MCP Client                                │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  LLM (Claude, GPT-4, etc.)                           │  │
│  │  • Sees: "Search APIs + hierarchy"                   │  │
│  │  • Writes: JS code to search/execute                 │  │
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
│  ┌─────────────────┐                                       │
│  │  Auth Managers  │  Platform-specific Auth (OAuth2/Key)  │
│  │  • Auto-auth    │  → In-memory, zero disk I/O           │
│  │  • Auto-refresh │                                       │
│  └─────────────────┘                                       │
└──────────────┬──────────────────────┬───────────────────────┘
               │                      │
               ▼                      ▼
    spec/*.resolved.json          Aruba Central, Mist,
         (Local specs)            Clearpass, SDC, UXI APIs
```

## How It Works

### 1. Authentication (Automatic)
On startup, CentralMind uses your configured credentials (client ID/secret or API tokens) for each platform to authenticate. Tokens are held in memory and auto-refreshed before expiry. No manual token management required.

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

Runs in hardened Deno sandbox with **no network access** — only reads the local spec file.

### 4. Execute (Action)
LLM chains API calls:
```javascript
async () => {
  // Monitoring endpoint
  const aps = await central.request({ // or mist.request, sdc.request, clearpass.request
    path: '/network-monitoring/v1/aps',
    params: { limit: 5 }
  });
  return aps;
}
```

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

### 4. Resolve the OpenAPI Specs
```bash
# Resolve specs for the platforms you plan to use
python -m centralmind.spec_resolver spec/openAPI.json spec/openAPI.resolved.json
python -m centralmind.spec_resolver spec/mist.openapi.json spec/mist.resolved.json
python -m centralmind.spec_resolver spec/sdc.openapi.json spec/sdc.resolved.json
python -m centralmind.spec_resolver spec/clearpass-openapi.json spec/clearpass-openapi.resolved.json
python -m centralmind.spec_resolver spec/uxi.openapi.json spec/uxi.resolved.json
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
        "MIST_API_TOKEN": "your-mist-token",
        "SDC_API_URL": "https://<your-sdc-url>",
        "SDC_API_KEY": "your-sdc-key",
        "CLEARPASS_BASE_URL": "https://<your-clearpass-url>",
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
| `MIST_API_TOKEN` | HPE Juniper Mist API Token | |
| `SDC_API_URL` | HPE Networking Security Director Cloud API URL | |
| `SDC_API_KEY` | HPE Networking Security Director Cloud API Key | |
| `CLEARPASS_BASE_URL`| HPE Aruba Clearpass API base URL | |
| `CLEARPASS_CLIENT_ID`| HPE Aruba Clearpass OAuth2 client ID | |
| `CLEARPASS_CLIENT_SECRET`| HPE Aruba Clearpass OAuth2 client secret | |
| `UXI_CLIENT_ID`| HPE Aruba Networking UXI OAuth2 client ID | |
| `UXI_CLIENT_SECRET`| HPE Aruba Networking UXI OAuth2 client secret | |
| `UXI_HOST`| HPE Aruba Networking UXI API host | `api.capenetworks.com` |
| `UXI_VERIFY_SSL`| Verify SSL certificates for UXI | `true` |
| `AOSCX_USERNAME`| AOS-CX administrator username | |
| `AOSCX_PASSWORD`| AOS-CX administrator password | |
| `AOSCX_VERIFY_SSL`| Verify SSL certificates for AOS-CX | `false` |
| `CENTRALMIND_API_MODE` | `readonly` / `readwrite` / `all` | `readonly` |
| `CENTRALMIND_RATE_LIMIT` | Requests per minute (0=unlimited) | `30` |
| `CENTRALMIND_MAX_CONCURRENT`| Max parallel sandbox processes | `5` |

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v --cov     # Run tests with coverage
ruff check src/ tests/               # Lint
ruff format src/ tests/              # Format
```

## Project Structure

```text
central-mind/
├── src/centralmind/       # Source code
│   ├── __main__.py        # CLI entry point
│   ├── auth.py            # OAuth2/Token management
│   ├── config.py          # Pydantic settings
│   ├── sandbox.py         # Deno sandbox (search + execute)
│   ├── server.py          # MCP server handlers
│   ├── spec_indexer.py    # OpenAPI → tiny token index
│   └── spec_resolver.py   # $ref resolver
├── tests/                 # Tests
├── spec/                  # OpenAPI specs + resolver outputs
├── pyproject.toml
└── README.md
```

## License

MIT
