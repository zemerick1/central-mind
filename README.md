# CentralMind MCP Server

**Code Mode MCP** for enterprise networking APIs — exposing thousands of endpoints across multiple platforms in a highly efficient token footprint.

CentralMind is a fork of [**MistMind**](https://github.com/nagarjun226/mistmind) by [@nagarjun226](https://github.com/nagarjun226). ...

## Dynamic Enrichment Phase (New Feature)

**Dynamic Enrichment Phase** — automatically enriches API results with blast radius, business impact, topology correlations, client impact, and recommendations.

After any `execute_*` call, if enabled, the server runs a controlled second code-mode pass where the LLM can make 1-3 additional JS calls to gather context (LLDP neighbors, client counts, switch details, alerts, etc.) and attach structured `_enrichment` data.

**Configuration** (in `.env` or MCP client env):
```env
CENTRALMIND_ENABLE_ENRICHMENT=true
CENTRALMIND_MAX_ENRICHMENT_CALLS=3
```

This keeps the philosophy of **code mode first** while delivering significantly richer outputs.

---

> # ⚠️🚨 READ THIS BEFORE USING `readwrite` MODE 🚨⚠️
> (original warning remains)

(The rest of the original README content follows below this new section.)
