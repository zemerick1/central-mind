# CentralMind MCP Server

**Code Mode MCP** for the Aruba Central API — **718+ endpoints** in **~800 tokens**.

CentralMind is a fork of [**MistMind**](https://github.com/nagarjun226/mistmind) by [@nagarjun226](https://github.com/nagarjun226), adapted for the Aruba Central API. The architecture, sandbox design, and progressive disclosure pattern all come from MistMind — CentralMind just points them at a different API.

---

> # ⚠️🚨 READ THIS BEFORE USING `readwrite` MODE 🚨⚠️
>
> **LLMs make mistakes. Period. They will hallucinate endpoints, invent parameters, and confidently execute destructive API calls that look perfectly reasonable.**
>
> When you set `CENTRALMIND_API_MODE=readwrite`, you are giving an AI **unsupervised write access** to your production Aruba Central environment. That means it can:
>
> - **Delete SSIDs, VLANs, firewall rules, and certificates**
> - **Push broken configurations to live access points and gateways**
> - **Modify authentication profiles, RADIUS servers, and security policies**
> - **Overwrite port profiles and take down entire switch stacks**
> - **Create, modify, or destroy any resource exposed by the 718+ API endpoints**
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

CentralMind takes that same approach and applies it to **Aruba Central**, which has its own challenges:
- **718+ endpoints** split across monitoring (`/network-monitoring/v1/...`) and configuration (`/network-config/v1/...`) domains
- **OAuth2 `client_credentials`** auth instead of API keys
- **A fragmented spec** that needed consolidation from 200+ individual Postman collections

The progressive disclosure pattern from MistMind makes all of this manageable:
- **Initial:** ~800 tokens for the full API hierarchy
- **Search:** LLM writes JS to explore the 25MB resolved spec
- **Execute:** LLM chains API calls with full OpenAPI context

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Claude Desktop / MCP Client                                │
│  ┌──────────────────────────────────────────────────────┐  │
│  │  LLM (Claude, GPT-4, etc.)                           │  │
│  │  • Sees: "Search API (718+ endpoints) + hierarchy"   │  │
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
│  │    OpenAPI      │  │  • --allow-net=<central host>    │ │
│  │  • Generates    │  │  • Rate limiting (30/min)        │ │
│  │    hierarchy    │  │  • Token isolation (IIFE)        │ │
│  │  • ~800 tokens  │  │  • Output scrubbing              │ │
│  └─────────────────┘  └──────────────────────────────────┘ │
│  ┌─────────────────┐                                       │
│  │  CentralAuth    │  OAuth2 client_credentials grant      │
│  │  • Auto-auth    │  → Bearer token (2hr TTL)             │
│  │  • Auto-refresh │  → In-memory, zero disk I/O           │
│  └─────────────────┘                                       │
└──────────────┬──────────────────────┬───────────────────────┘
               │                      │
               ▼                      ▼
    spec/openAPI.resolved.json    internal.api.central.arubanetworks.com
         (25MB, local)           (REST API, Bearer auth)
```

## How It Works

### 1. Authentication (Automatic)
On startup, CentralMind uses your `client_id` and `client_secret` to obtain a Bearer token via OAuth2 `client_credentials` grant. Tokens are held in memory and auto-refreshed before expiry. No manual token management required.

### 2. Index Generation (Initialization)
```python
from centralmind.spec_indexer import generate_index_from_file

index = generate_index_from_file("spec/openAPI.resolved.json")
# → ~800 token summary: tags, categories, auth, pagination
```

### 3. Search (Discovery)
LLM writes JavaScript to explore the spec:
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
  const aps = await central.request({
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
- Aruba Central API credentials (client ID + secret)

### 2. Install
```bash
git clone <repo-url>
cd central-mind
pip install -e .
```

### 3. Configure
```bash
cp .env.example .env
# Edit .env with your Aruba Central client ID and secret
```

### 4. Resolve the OpenAPI Spec
```bash
python -m centralmind.spec_resolver spec/openAPI.json spec/openAPI.resolved.json
```

### 5. Add to Claude Desktop
```json
{
  "mcpServers": {
    "centralmind": {
      "command": "python",
      "args": ["-m", "centralmind"],
      "env": {
        "CENTRAL_BASE_URL": "https://internal.api.central.arubanetworks.com",
        "CENTRAL_CLIENT_ID": "your-client-id",
        "CENTRAL_CLIENT_SECRET": "your-client-secret",
        "CENTRALMIND_API_MODE": "readonly"
      }
    }
  }
}
```

See `claude_desktop_config.example.json` for a full example.

## Security

CentralMind is built with defense-in-depth:

- **Deno sandbox isolation** — Each execution is a fresh process
- **IIFE token closure** — Bearer token lives in closure scope, unreachable by user code
- **stdin token passing** — Token never written to disk or source files
- **Network allowlist** — Execute mode only reaches the configured Aruba Central host
- **API mode enforcement** — `readonly` blocks all writes (server-side, not bypassable)
- **Rate limiting** — 30 req/min, max 5 concurrent (configurable)
- **Output scrubbing** — Token removed from all stdout/stderr/errors
- **OAuth2 in-memory** — Access tokens held in memory only, auto-refreshed

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `CENTRAL_BASE_URL` | Aruba Central API base URL | `https://internal.api.central.arubanetworks.com` |
| `CENTRAL_CLIENT_ID` | OAuth2 client ID | (required) |
| `CENTRAL_CLIENT_SECRET` | OAuth2 client secret | (required) |
| `CENTRALMIND_API_MODE` | `readonly` / `readwrite` / `all` | `readonly` |
| `CENTRALMIND_RATE_LIMIT` | Requests per minute (0=unlimited) | `30` |
| `CENTRALMIND_MAX_CONCURRENT` | Max parallel sandbox processes | `5` |
| `CENTRALMIND_SPEC_PATH` | Custom OpenAPI spec path | `spec/openAPI.resolved.json` |

## Development

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v --cov     # Run tests with coverage
ruff check src/ tests/               # Lint
ruff format src/ tests/              # Format
```

## Project Structure

```
central-mind/
├── src/centralmind/       # Source code
│   ├── __main__.py        # CLI entry point
│   ├── auth.py            # OAuth2 token management
│   ├── config.py          # Pydantic settings
│   ├── sandbox.py         # Deno sandbox (search + execute)
│   ├── server.py          # MCP server handlers
│   ├── spec_indexer.py    # OpenAPI → ~800 token index
│   └── spec_resolver.py   # $ref resolver
├── tests/                 # Tests
├── spec/                  # OpenAPI spec + resolver output
├── pyproject.toml
└── README.md
```

## License

MIT
