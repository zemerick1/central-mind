# CentralMind MCP Server

Intelligent Model Context Protocol server for network infrastructure.

## Dynamic Enrichment Phase

**New Feature**: After primary code-mode execution, the server can now run an optional Dynamic Enrichment Phase.

This phase uses a second controlled code-mode pass to analyze results for:
- Blast radius and business impact
- Topology correlations (LLDP, switches, neighbors)
- Client impact
- Risk assessment
- Actionable recommendations

**Config**:
```env
CENTRALMIND_ENABLE_ENRICHMENT=true
CENTRALMIND_MAX_ENRICHMENT_CALLS=2
```

This keeps the "code mode first" philosophy while delivering much richer outputs.
