## Dynamic Enrichment Phase

After primary JS code execution, the MCP server can now optionally run a **Dynamic Enrichment Phase**.

This phase uses a second controlled code-mode pass to analyze results for:
- Blast radius and business impact
- Topology correlations (LLDP, switches, RF neighbors)
- Client impact
- Root cause hints
- Recommended actions

**Config:**
`CENTRALMIND_ENABLE_ENRICHMENT=true`

Keeps "code mode first" philosophy intact.