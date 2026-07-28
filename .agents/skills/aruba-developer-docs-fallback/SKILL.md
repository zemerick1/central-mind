---
name: aruba-developer-docs-fallback
title: Aruba Developer Docs llms.txt — last-resort endpoint discovery
description: |
  LAST RESORT / NUCLEAR OPTION — invoke this skill ONLY after all other
  avenues have been exhausted: the MCP server tools returned no match,
  the relevant platform-specific skills have been tried, and you still
  cannot find the API endpoint or documentation reference you need.

  This skill fetches the Aruba Developer Portal's `llms.txt` index
  (https://developer.arubanetworks.com/llms.txt) — a ~2800-link,
  ~550 KB master catalog spanning AOS-CX, Aruba Central, ClearPass
  Policy Manager, and UXI. The index itself is large; the `.md` pages
  it links to are even larger. **Both will obliterate your context
  window if consumed carelessly.** Do NOT reach for this skill when a
  scoped MCP tool or an existing skill can answer the question.

  Trigger ONLY when you have already tried the relevant MCP tools
  (e.g. `central_invoke_tool`, `clearpass_invoke_tool`, etc.) and
  received errors indicating the endpoint does not exist, is not
  registered, or returned no usable result — and the operator still
  needs the answer. This is the break-glass, not the first call.
platforms: [central, clearpass, aoscx, uxi]
tags: [fallback, docs, api-reference, last-resort, nuclear]
tools: []
---

# Aruba Developer Docs — last-resort endpoint discovery

## Objective

When every MCP tool and existing skill has failed to locate an API
endpoint, payload shape, or documentation reference, fall back to the
Aruba Developer Portal's machine-readable index (`llms.txt`) to find
the correct `.md` documentation page — then fetch **only** the single
relevant page to answer the operator's question.

This is a **context-window-hostile** operation. The index alone is
~550 KB / ~2800 links. Each linked `.md` page can be tens of KB more.
The entire point of this skill is to make that cost deliberate, bounded,
and only paid when nothing else works.

## ⚠️  CRITICAL WARNINGS

> **DO NOT** use this skill as a first resort. It exists because the
> MCP server's tool catalog does not cover every Aruba API endpoint.
> The vast majority of questions can be answered by the registered MCP
> tools and the other skills in this project.

> **DO NOT** fetch more than one or two `.md` pages per invocation.
> Each page can be 10–50 KB of markdown. Fetching several will flood
> your context window and degrade reasoning quality for the rest of
> the conversation.

> **DO NOT** dump the raw `llms.txt` content to the operator. It is
> an internal lookup index, not a user-facing deliverable.

## Prerequisites

Before invoking this skill, **all** of the following must be true:

- You have already attempted the relevant MCP tool(s) for the platform
  in question (e.g. `central_search_tool`, `clearpass_search_tool`,
  `mist_search_tool`, etc.) and they failed or returned no result.
- You have checked whether an existing skill covers the topic (e.g.
  `central-vlan-configuration`, `central-qos-policy`,
  `clearpass-policy-walker`, etc.) and none of them answer the question.
- The operator still needs the endpoint / payload / documentation
  reference and cannot proceed without it.
- You can articulate a specific search term or API path fragment to
  grep for in the index — do NOT fetch the index "just to browse."

## Procedure

### Step 0 — Confirm exhaustion (mandatory gate)

Before proceeding, verify that you have genuinely exhausted other
options. Ask yourself:

| Check | Satisfied? |
|---|---|
| Tried the platform's MCP tool and got an error or empty result | ✅ |
| Checked existing skills for coverage | ✅ |
| Have a specific search term (endpoint path, resource name, keyword) | ✅ |
| No other tool or skill can answer this | ✅ |

If any check is ❌, **STOP** and go use the appropriate tool or skill
instead. Do not proceed.

### Step 1 — Fetch the llms.txt index

**Action:** Fetch `https://developer.arubanetworks.com/llms.txt`
using `read_url_content`.

**Why:** This is the master index of all Aruba developer documentation
pages. It contains ~2800 links organized under four top-level sections:

| Section | Coverage |
|---|---|
| `# AOS-CX Documentation` | AOS-CX switch REST API (on-box) — guides + full endpoint reference |
| `# New Central Documentation` | Aruba Central cloud API — guides + endpoint reference |
| `# ClearPass Policy Manager Documentation` | ClearPass REST API — guides + endpoint reference |
| `# User Experience Insight Documentation` | UXI sensor API — guides + endpoint reference |

**Expected result:** A large markdown document with categorized `.md`
links. Do NOT read the entire file. Proceed immediately to Step 2.

### Step 2 — Search the index for the target endpoint / topic

**Action:** Grep or scan the fetched content for your specific search
term — an API path fragment, resource name, or keyword.

**Strategy for efficient searching:**
- If you know the API resource (e.g. `vlans`, `acls`, `ports`), search
  for that term in the link text or URL path.
- If you know the HTTP method context, look for links whose URL
  contains `get_`, `post_`, `put_`, `patch_`, or `delete_` prefixes
  in the reference section.
- The **Guides** subsections contain conceptual docs and workflows.
  The **API Reference** subsections contain per-endpoint docs. For
  endpoint discovery, focus on the API Reference section.

**Expected result:** One or a small number of matching `.md` URLs that
look relevant to the operator's question.

**If no match:** Tell the operator plainly that the endpoint does not
appear in the Aruba developer documentation index. Do NOT guess or
fabricate an endpoint.

### Step 3 — Fetch ONLY the single most relevant .md page

**Action:** Fetch the single best-matching `.md` URL using
`read_url_content`.

**IMPORTANT:** Fetch exactly ONE page. If you are uncertain between
two candidates, fetch the more specific one first. Only fetch a second
page if the first was clearly wrong.

**Why:** Each `.md` page contains the actual endpoint documentation —
path, parameters, request/response schema, examples. This is what you
need to answer the operator's question.

**Expected result:** A markdown document with the endpoint details.
Extract the specific information the operator needs (path, method,
payload shape, required parameters) and discard the rest.

### Step 4 — Deliver a concise answer

**Action:** Synthesize the fetched documentation into a concise,
actionable answer for the operator. Include:

- The exact API endpoint path and HTTP method
- Required parameters and their types
- A minimal example payload if applicable
- The source URL for the operator's reference

**DO NOT** paste the entire fetched `.md` page into your response.
Extract only what is needed.

## Decision matrix

| Condition | Action |
|---|---|
| MCP tool returned the data the operator needs | **DO NOT** use this skill — you already have the answer. |
| MCP tool returned an error but another skill covers the topic | Use that skill instead. |
| MCP tool returned an error, no skill covers it, and you have a specific search term | Proceed with this skill. |
| You found multiple candidate `.md` links in the index | Fetch the most specific one first. Ask before fetching a second. |
| No match found in the index | Tell the operator — do not hallucinate endpoints. |
| The fetched `.md` page is insufficient | You may fetch ONE more candidate page, then stop. |

## When NOT to use this skill

- **An MCP tool answered the question.** Even partially — work with
  what you have before reaching for external docs.
- **An existing skill covers the topic.** Check the skill list first.
- **You're "just browsing" or "exploring the API."** This is not a
  browsing tool. Have a specific target.
- **The operator asked about Mist / Juniper APIs.** This index covers
  Aruba (HPE) platforms only — AOS-CX, Central, ClearPass, UXI.
  Mist/Juniper docs are not here.

## Examples

> "The MCP server doesn't have a tool for the Central `/airmatch/`
> endpoint — can you find the API docs for it?"

> "I need the ClearPass endpoint for managing certificates but
> `clearpass_invoke_tool` says it doesn't exist."

> "What's the AOS-CX REST API path for configuring OSPF areas? The
> MCP tool doesn't cover on-box switch APIs."

> "I can't find the UXI sensor test-results endpoint in any of the
> registered tools."

## Output formatting

Return a focused answer containing:

1. **Endpoint:** `METHOD /path/to/resource`
2. **Key parameters:** bullet list of required/notable params
3. **Example payload:** (if applicable) a minimal JSON snippet
4. **Source:** clickable link to the `.md` page you fetched
5. **Caveat:** note that this came from external docs, not the MCP
   tool catalog, so the operator should verify against their platform
   version.
