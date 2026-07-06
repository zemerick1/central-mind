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
- **HPE Axis Security** (70 endpoints)

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

CentralMind takes that same approach and applies it to **HPE Aruba Networking Central, HPE Juniper Mist, HPE Networking Security Director Cloud, HPE Aruba Clearpass, HPE Aruba Networking UXI, and Axis Security**, which have their own challenges:
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
         (Local specs)            Clearpass, SDC, UXI, Axis APIs
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
python -m centralmind.spec_resolver spec/axis.openapi.json spec/axis.resolved.json
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
        "AXIS_APITOKEN": "your-axis-token",
        "CENTRALMIND_API_MODE": "readonly"
      }
    }
  }
}
```

### 5b. Other MCP Clients (Antigravity, Cursor, etc.)

Most MCP-compatible IDEs use a `mcp_config.json` or `mcp_settings.json` file. The format is identical — add a `centralmind` entry to the `mcpServers` object as shown above.

> **Note:** Environment variables set in `env` take priority over a `.env` file. The `.env` file works if the MCP client's working directory is the project root, but most clients don't guarantee that — so setting credentials in the MCP config is the reliable approach.
>
> The first time CentralMind runs, it migrates whatever credentials it finds (`.env` or environment variables) into a client called `"default"` in the credential store described below. After that, `.env` is no longer consulted — manage credentials (including this one) through `centralmind admin` or the `list_clients`/`switch_client` tools.

## Multi-Client Setup

If you work across multiple network environments — your own test/lab setup, multiple customer environments, or both — CentralMind can hold all of their credentials at once and let an MCP client switch between them without restarting the server. (Note: this is unrelated to Aruba Central's own "MSP mode" feature — "client" here just means one set of platform credentials.)

### Managing clients

```bash
centralmind admin              # launches a local (127.0.0.1-only) web UI, default port 8787
centralmind admin --port 9000  # custom port
```

The console prints a one-time URL containing an admin token (similar to Jupyter) — open it in a browser to add, edit, test, or delete clients. Each client is a full credential set (Central, ClearPass, Mist, Axis, SDC, UXI, AOS-CX). Credentials are stored Fernet-encrypted at `~/.centralmind/clients.json` (key at `~/.centralmind/secret.key`) — override the location with `CENTRALMIND_CLIENTS_FILE` if you need it elsewhere, but keep the data and key files together and protected.

`*_base_url` fields must be a full `http://`/`https://` URL and `*_host` fields must be a bare hostname (no scheme or path) — the form rejects and explains malformed values on save instead of letting them surface later as a confusing auth failure.

### Switching clients from the LLM

Two tools are always available regardless of which platforms are configured:

- **`list_clients`** — lists configured clients, which platforms each has credentials for, each one's effective API mode, and which one is active for your current session.
- **`switch_client`** — switches the active client for your session. All subsequent `search_*`/`execute_*` calls target that client's credentials until you switch again.

```
> list_clients
[{"id": "…", "name": "Acme Corp", "platforms": ["central", "mist"], "api_mode": "readonly", "active": true, "default": true}, …]

> switch_client client_id=<other-client-id>
{"active_client": "…", "name": "Contoso Networks", "platforms": ["central"]}
```

Switching is scoped to your own MCP connection — if two techs are connected to the same server (see Network Access below), switching your active client never affects theirs.

### Per-client API access mode

Each client can optionally override the global `CENTRALMIND_API_MODE` (readonly/readwrite/all) from its edit page in `centralmind admin` — e.g. so a personal test/lab client can have `readwrite` access while every real customer client stays `readonly`, all on the same running server.

**The server's own launch-time mode is always a hard ceiling.** A client's override can only narrow access further, never grant more than the server itself allows — setting a client to `all` while the server is launched with `CENTRALMIND_API_MODE=readonly` still resolves to `readonly` for that client. There's no way to escalate a client past the server's own setting; the only way to actually get broader access is to launch the server itself in that mode. `list_clients` always reports the real effective mode after this ceiling is applied, so there's no guessing.

Changing a client's mode (or credentials) in the admin UI takes effect immediately for that client's next tool call — no server restart required.

## Network Access

By default CentralMind runs over stdio as a local subprocess of your MCP client. To make one instance reachable from other machines on your network, run it with the `http` transport (Streamable HTTP, per the current MCP spec):

```bash
centralmind --transport http --host 0.0.0.0 --port 8000
```

This is **HTTPS by default** — the first run auto-generates a self-signed certificate (see TLS Certificates below) — plus an API key (auto-generated on first run and persisted in the credential store; pass `--api-key <key>` to set your own). Every request must include `Authorization: Bearer <key>` — requests without it get `401 Unauthorized`. Treat this key like a password. The console prints the exact LAN-reachable URL to use.

Example remote client config:

```json
{
  "mcpServers": {
    "centralmind": {
      "url": "https://<this-machine-ip>:8000/mcp/",
      "headers": { "Authorization": "Bearer <api-key-from-console>" }
    }
  }
}
```

(For MCP clients that don't yet support a remote `url` directly, use the [`mcp-remote`](https://www.npmjs.com/package/mcp-remote) stdio-to-HTTP bridge.)

**Security notes:**
- The Bearer API key is the entire access boundary for the network transport — anyone with it and network access to the host can use every tool the active client has credentials for, including `readwrite`/`all` API modes if enabled. Rotate it (`--api-key`) if it may have leaked.
- The admin UI (`centralmind admin`) always binds to `127.0.0.1` regardless of the flags above — credential entry never goes out over the network.

### TLS Certificates

`--transport http` serves HTTPS unless told otherwise. Three ways to get a certificate:

1. **Do nothing (default).** A self-signed certificate is generated automatically on first use, covering `localhost`, this machine's hostname, and its detected local IP addresses. MCP clients will need to accept a trust warning (or be configured to skip cert verification) since it isn't signed by a CA anyone trusts.
2. **Import one from an enterprise or public CA** (e.g. your internal PKI, or a Let's Encrypt-obtained pair) — either:
   ```bash
   centralmind tls import --cert fullchain.pem --key privkey.pem
   ```
   or upload the same two files through the "TLS certificate" page in `centralmind admin`. Both validate that the private key actually matches the certificate before installing it, and reject expired certificates.
3. **Opt out of TLS entirely** with `--no-tls` (plain HTTP) — e.g. when a reverse proxy (nginx, Caddy, etc.) already terminates TLS in front of CentralMind.

Other useful commands:

```bash
centralmind tls status              # show the currently installed cert (self-signed vs imported, expiry)
centralmind tls generate --force    # rotate to a fresh self-signed certificate
centralmind --cert my.pem --key my.key --transport http   # use a cert/key for just this run, without installing it
```

Certificates live at `~/.centralmind/tls/` (override with `CENTRALMIND_TLS_DIR`), alongside the encrypted client credential store.

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
- **API mode enforcement** — `readonly` blocks all writes (server-side, not bypassable); a per-client override can only narrow this further, never exceed the server's own launch-time setting
- **Rate limiting** — 30 req/min, max 5 concurrent (configurable)
- **Output scrubbing** — Tokens removed from all stdout/stderr/errors
- **In-memory Auth** — Access tokens held in memory only
- **Encrypted credential store** — Multi-client credentials are Fernet-encrypted at rest (`~/.centralmind/`)
- **Bearer-token network auth** — The `http` transport rejects any request without the configured API key
- **HTTPS by default** — `http` transport auto-provisions a self-signed cert; imported certs are validated (key must match, must not be expired) before install
- **Loopback-only admin UI** — `centralmind admin` never binds beyond `127.0.0.1`, and is itself token-gated

## Configuration

These variables seed the initial `"default"` client on first run (see [Multi-Client Setup](#multi-client-setup)) — after that, `centralmind admin` or the `list_clients`/`switch_client` tools are the ongoing source of truth, not `.env`.

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
| `AXIS_APITOKEN`| Axis API token | |
| `AXIS_HOST`| Axis API host | `admin-api.axissecurity.com` |
| `AOSCX_USERNAME`| AOS-CX administrator username | |
| `AOSCX_PASSWORD`| AOS-CX administrator password | |
| `AOSCX_VERIFY_SSL`| Verify SSL certificates for AOS-CX | `false` |
| `CENTRALMIND_API_MODE` | `readonly` / `readwrite` / `all` | `readonly` |
| `CENTRALMIND_RATE_LIMIT` | Requests per minute (0=unlimited) | `30` |
| `CENTRALMIND_MAX_CONCURRENT`| Max parallel sandbox processes | `5` |
| `CENTRALMIND_CLIENTS_FILE`| Path to the encrypted multi-client credential store | `~/.centralmind/clients.json` |
| `CENTRALMIND_TLS_DIR`| Directory holding the `http` transport's TLS cert + key | `~/.centralmind/tls` |

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
├── src/centralmind/         # Source code
│   ├── __main__.py          # CLI entry point (serve + admin + tls subcommands)
│   ├── admin_web.py         # Loopback-only credential + TLS admin web UI
│   ├── auth.py              # OAuth2/Token management
│   ├── auth_middleware.py   # Bearer-token auth for the http transport
│   ├── clients_store.py     # Encrypted multi-client credential store
│   ├── config.py            # Pydantic settings (global server config)
│   ├── platform_factory.py  # Builds platform Auth instances from a credential profile
│   ├── sandbox.py           # Deno sandbox (search + execute)
│   ├── server.py            # MCP server handlers, multi-client aware
│   ├── spec_indexer.py      # OpenAPI → tiny token index
│   ├── spec_resolver.py     # $ref resolver
│   └── tls.py               # Self-signed cert generation + CA cert import for the http transport
├── tests/                   # Tests
├── spec/                    # OpenAPI specs + resolver outputs
├── pyproject.toml
└── README.md
```

## License

MIT
