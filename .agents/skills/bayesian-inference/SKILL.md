---
name: bayesian-inference
title: Bayesian Inference for root-cause analysis and decision-making under uncertainty
description: |
  Use when performing root-cause analysis, incident investigation, or decision-making under uncertainty with central-mind MCP tools on Aruba Central alerts, events, device state, or logs. Maintains evolving probabilistic beliefs (posteriors) over hypotheses such as hardware_issue, config_drift, transient_noise, wlan_density, software_bug. Provides rigorous Bayesian updates, most-likely hypothesis, confidence, and action recommendations. Call functions from scripts/bayesian_utils.py after parsing Central data into evidence.
platforms: [central]
tags: [analysis, troubleshooting, bayesian, root-cause]
tools: [search_central, execute_central, search_clearpass, execute_clearpass, search_mist, execute_mist]
---

# Bayesian Inference for root-cause analysis and decision-making under uncertainty

## Objective

Use this skill for any task involving noisy, partial, or streaming evidence from Aruba Central (via central-mind `search_central`, `execute_central`, or webhook-style data). It turns ad-hoc LLM reasoning into auditable, accumulating, probability-calibrated belief tracking.

## Prerequisites

- Access to `central-mind` MCP tools for raw data collection.
- The `scripts/bayesian_utils.py` module is available in the skill directory.
- The user has provided an incident, alert, or device context (e.g., site ID, device MAC, or alert ID) to track.

## Procedure

### Step 1 — Gather Raw Evidence

**Tool:** `search_central(query=...)` or `execute_central(command=...)`
**Why:** Collect recent alerts, events, logs, CPU/memory usage, reboot reasons, or client complaints for the affected device or site.
**Expected result:** Raw log or event entries indicating operational issues.
**If anomaly:** If no raw data is returned, notify the user and ask for specific identifiers or context.

### Step 2 — Parse Evidence into Structured Form

**Tool:** Standard Python parsing/dict creation.
**Why:** Structure raw alerts/logs into a normalized evidence dictionary with likelihood supports.
**Expected result:** An evidence dictionary following the Evidence Schema.
**If anomaly:** If data is extremely weak or highly ambiguous, assign a lower weight (e.g., `0.5` or `1.0`) and more uniform support scores.

### Step 3 — Perform Bayesian Update

**Tool:** Python execution calling `update_beliefs` in `scripts/bayesian_utils.py`.
**Why:** Perform exact Dirichlet conjugate updates to compute the new posterior probability distribution.
**Expected result:** An updated posterior dictionary containing the new hypothesis probabilities, confidence score, and history.
**If anomaly:** If loading a previous posterior fails, initialize a new update with `previous_posterior=None` to start from a uniform prior.

### Step 4 — Get Next Action Recommendation

**Tool:** Python execution calling `get_action_recommendation` and `compute_value_of_information` in `scripts/bayesian_utils.py`.
**Why:** Get a principled next step based on expected utility and the Value of Information (VoI).
**Expected result:** A recommended action, reasoning, and VoI classification.
**If anomaly:** If action space is undefined or ambiguous, fall back to `"monitor_closely"`.

---

## Python Code Usage Snippet

Copy and execute the following snippet to run the Bayesian update loop:

```python
import sys

# Ensure the skill folder is in sys.path to import utilities
skill_path = "/home/user/central-mind/.agents/skills/bayesian-inference"
if skill_path not in sys.path:
    sys.path.append(skill_path)

from scripts.bayesian_utils import (
    update_beliefs, 
    get_action_recommendation, 
    compute_value_of_information
)

# Example evidence dictionary (normally populated from central-mind tools)
evidence = {
    "supports": {
        "hardware_issue": 0.85,
        "config_drift": 0.25,
        "transient_noise": 0.15,
        "wlan_density": 0.10,
        "software_bug": 0.40
    },
    "weight": 3.0,
    "raw_summary": "AP Rebooted unexpectedly. High CPU observed before. Some client complaints.",
}

# 1. Update beliefs (use previous_posterior dict if iterating)
posterior = update_beliefs(previous_posterior=None, evidence=evidence)

# 2. Get recommendations
recommendation = get_action_recommendation(posterior)

# 3. Calculate Value of Information (VoI)
voi = compute_value_of_information(posterior)

print("New Posterior Probabilities:", posterior["hypotheses"])
print("Recommended Action:", recommendation["recommended_action"])
print("Reasoning:", recommendation["reason"])
print("VoI Status:", voi["recommendation"])
```

---

## Evidence Schema

The quality of the posterior depends on good evidence structure. Format each evidence input as follows:

```json
{
  "context_key": "site-47020865885560832:alert-ea017ba0",
  "source": "central_alert",
  "supports": {
    "hardware_issue": 0.85,
    "config_drift": 0.25,
    "transient_noise": 0.15,
    "wlan_density": 0.10,
    "software_bug": 0.40
  },
  "weight": 3.0,
  "raw_summary": "AP Rebooted unexpectedly. High CPU observed before. Some client complaints.",
  "timestamp": "2026-07-13T13:23:10Z"
}
```

- **context_key**: A unique string linking related events (e.g., `"site-<id>:alert-<id>"` or `"device-mac-<mac>:reboot-incident"`).
- **source**: Source category (e.g., `"central_alert"`, `"device_logs"`, `"user_report"`).
- **supports**: Dict of support scores in `[0.0, 1.0]` for each hypothesis. A score of `0.5` represents neutral support.
- **weight**: Effective strength of this evidence (equivalent to pseudo-observations, typically `1.0` to `5.0`).
- **raw_summary**: Human-readable explanation of why these support scores were assigned.
- **timestamp**: ISO 8601 formatted timestamp.

---

## Decision matrix

The following table summarizes the recommendation policy used by `get_action_recommendation` based on the most likely hypothesis and confidence:

| Condition | Recommended Action | Action Details / Mitigation |
|---|---|---|
| Most likely is `hardware_issue`, `software_bug`, or `cluster_issue` AND confidence > 0.65 | `escalate_oncall` | Escalate to on-call engineer; trigger immediate notification. |
| Most likely is `transient_noise` AND confidence > 0.60 | `acknowledge_transient` | Acknowledge/dismiss alert in Central; low action needed. |
| Most likely is `config_drift` or `wlan_density` | `enrich_more_data` | Call additional `central-mind` tools (e.g., check change logs or RF health). |
| Confidence is low (< 0.55) or other cases | `monitor_closely` | Keep tracking the incident context; wait for more evidence. |

---

## Output formatting

When presenting the results of a Bayesian update back to the operator, always include:

1. **Incident Summary**: The context key, number of evidence points accumulated, and timestamp of the latest update.
2. **Current Posterior Table**: A markdown table showing all hypotheses, their posterior probabilities, and Dirichlet alpha parameters.
3. **Recommendation**: The recommended action and the calculated reason/confidence.
4. **Evidence Log**: A brief bulleted log of the raw summaries of evidence ingested so far.

Example output:

### Bayesian Root-Cause Analysis
- **Context Key**: `device-00:11:22:33:44:55:reboot-incident`
- **Evidence Count**: 2 points ingested
- **Confidence**: 76.5%

| Hypothesis | Posterior Probability | Alpha Parameter |
|---|---|---|
| **hardware_issue** | **0.7650** | 4.55 |
| `software_bug` | `0.1120` | 1.95 |
| `config_drift` | `0.0630` | 1.25 |
| `transient_noise` | `0.0400` | 1.15 |
| `wlan_density` | `0.0200` | 1.10 |

**Recommendation**: `escalate_oncall`
> High confidence (77%) in hardware_issue — AP rebooted with low voltage warning. Escalate to replace hardware.

---

## Examples

- *"Investigate the recent reboot of AP-01 at site-4702"*
- *"Perform a Bayesian root-cause analysis on the flapping alert for device MAC 00:11:22:33:44:55"*
- *"What is the most likely reason for the authentication failures at Dallas site? Accumulate the logs into a Bayesian posterior."*