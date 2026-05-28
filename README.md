# CentralMind MCP Server

## Dynamic Enrichment Phase - IMPLEMENTED ✓

The **Dynamic Enrichment Phase** is now fully active on this branch.

After every primary `execute_*` tool call, if enabled, the server runs an enrichment analysis pass that automatically adds a structured `_enrichment` object containing:

- `impact_summary`
- `blast_radius` (Low/Medium/High/Critical)
- `client_impact`
- `correlations`
- `risks`
- `recommendations`

This gives you blast radius, client impact, and topology analysis without manual prompting.

**Configuration**
```env
CENTRALMIND_ENABLE_ENRICHMENT=true   # default
CENTRALMIND_MAX_ENRICHMENT_CALLS=3
```

The feature is implemented and the branch is in a working state.