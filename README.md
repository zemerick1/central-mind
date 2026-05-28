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
> When you set `CENTRALMIND_API_MODE=readwrite`, you are giving an AI **unsupervised write access** to your production networking environments. ...

## New Feature: Dynamic Enrichment Phase (on this branch)

**Dynamic Enrichment** automatically adds blast radius, business impact, correlations, root cause hints, and recommendations after primary data retrieval.

### How it works
1. LLM performs primary data fetch via code mode (JS)
2. Optional **enrichment phase** triggers (configurable)
3. Same LLM gets a focused prompt + raw result
4. It performs 1-3 lightweight follow-up JS calls to gather context (topology, LLDP, clients, alerts, etc.)
5. Result is merged into `_enrichment` / `_impact` block

This keeps **code mode first** while making outputs much more intelligent.

## Configuration

New setting:
- `CENTRALMIND_ENABLE_ENRICHMENT` = `true` (default on this branch)

...