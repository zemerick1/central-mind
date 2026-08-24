#!/usr/bin/env python3
"""
Bayesian Inference utilities for central-mind MCP server and agentic workflows.

Lightweight, exact conjugate updates (Dirichlet) for multi-hypothesis belief tracking
on Aruba Central events, alerts, and incidents.

Usage from LLM/agent:
    from scripts.bayesian_utils import update_beliefs, get_action_recommendation

    posterior = update_beliefs(previous_posterior, evidence_dict)
    action = get_action_recommendation(posterior)
"""

from __future__ import annotations
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


def _normalize_dict(d: Dict[str, float]) -> Dict[str, float]:
    """Normalize values to sum to 1.0."""
    total = sum(d.values())
    if total <= 0:
        return {k: 1.0 / len(d) for k in d}
    return {k: round(v / total, 4) for k, v in d.items()}


def update_beliefs(
    previous_posterior: Optional[Dict[str, Any]],
    evidence: Dict[str, Any],
    hypothesis_space: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Perform a Bayesian update using Dirichlet conjugate prior.

    previous_posterior: dict from a previous call to this function (or None for first update)
    evidence: {
        "supports": {"hardware_issue": 0.85, "config_drift": 0.3, ...},  # [0,1] support per hyp
        "weight": 2.0,   # effective strength of this evidence (pseudo-observations)
        "raw_summary": "...",  # optional for audit
        ...
    }
    hypothesis_space: list of hypothesis names. If None, inferred from evidence + previous.

    Returns new posterior dict with:
      - hypotheses: {hyp: probability}
      - alphas: {hyp: Dirichlet alpha parameter}
      - most_likely, confidence, evidence_count, last_evidence, updated_at
    """
    if hypothesis_space is None:
        if previous_posterior and "alphas" in previous_posterior:
            hypothesis_space = list(previous_posterior["alphas"].keys())
        else:
            hypothesis_space = list(evidence.get("supports", {}).keys()) or [
                "hardware_issue", "config_drift", "transient_noise",
                "wlan_density", "software_bug", "cluster_issue"
            ]

    # Initialize or load previous alphas (Dirichlet parameters)
    if previous_posterior and "alphas" in previous_posterior:
        alphas = np.array([previous_posterior["alphas"].get(h, 1.0) for h in hypothesis_space])
        evidence_count = previous_posterior.get("evidence_count", 0)
    else:
        alphas = np.ones(len(hypothesis_space))  # uniform prior (weak)
        evidence_count = 0

    # Get support scores from evidence (default neutral 0.5)
    supports = evidence.get("supports", {})
    weight = float(evidence.get("weight", 2.0))  # how much this evidence counts

    for i, hyp in enumerate(hypothesis_space):
        support = float(supports.get(hyp, 0.5))
        # Add pseudo-counts proportional to support * weight
        # This is a practical approximation to incorporating soft likelihood evidence
        alphas[i] += support * weight

    # Compute posterior mean probabilities
    total_alpha = alphas.sum()
    probs = alphas / total_alpha

    new_posterior = {
        "hypotheses": dict(zip(hypothesis_space, probs.round(4).tolist())),
        "alphas": dict(zip(hypothesis_space, alphas.round(2).tolist())),
        "most_likely": hypothesis_space[int(np.argmax(probs))],
        "confidence": round(float(np.max(probs)), 4),
        "evidence_count": evidence_count + 1,
        "last_evidence": {
            "supports": supports,
            "weight": weight,
            "raw_summary": evidence.get("raw_summary", ""),
            "timestamp": evidence.get("timestamp", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        },
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }

    return new_posterior


def get_action_recommendation(
    posterior: Dict[str, Any],
    available_actions: Optional[List[str]] = None,
    utilities: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Simple expected-utility recommendation based on current posterior.

    You can extend this with full Value of Information later.

    available_actions: e.g. ["enrich_more_data", "notify_critical", "acknowledge_transient", "escalate_oncall"]
    utilities: rough costs/benefits for each action under each hypothesis (higher = better)
    """
    if available_actions is None:
        available_actions = [
            "enrich_more_data",
            "notify_critical",
            "acknowledge_transient",
            "escalate_oncall",
            "monitor_closely"
        ]

    most_likely = posterior.get("most_likely", "transient_noise")
    confidence = posterior.get("confidence", 0.5)
    probs = posterior.get("hypotheses", {})

    # Default simple policy (replace with your real utilities/costs)
    if most_likely in ("hardware_issue", "software_bug", "cluster_issue") and confidence > 0.65:
        recommended = "escalate_oncall"
        reason = f"High confidence ({confidence:.0%}) in {most_likely} — escalate to avoid outage."
    elif most_likely == "transient_noise" and confidence > 0.6:
        recommended = "acknowledge_transient"
        reason = f"Likely transient noise ({confidence:.0%}) — low action needed."
    elif most_likely in ("config_drift", "wlan_density"):
        recommended = "enrich_more_data"
        reason = f"Possible {most_likely} — gather more context before acting."
    else:
        recommended = "monitor_closely"
        reason = f"Uncertain ({confidence:.0%} on {most_likely}) — continue monitoring."

    return {
        "recommended_action": recommended,
        "reason": reason,
        "most_likely_hypothesis": most_likely,
        "confidence": confidence,
        "full_posterior": posterior.get("hypotheses", {}),
        "note": "This is a starting policy. Customize utilities for your environment."
    }


def compute_value_of_information(
    posterior: Dict[str, Any],
    candidate_actions: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Very lightweight VoI approximation.
    Estimates expected reduction in uncertainty (entropy) if we took a particular action
    that gave perfect information about the true hypothesis.

    In practice: high VoI → worth calling more central-mind tools or asking clarifying questions.
    """
    probs = np.array(list(posterior.get("hypotheses", {}).values()))
    if len(probs) == 0:
        return {"voi": 0.0, "recommendation": "no_value"}

    # Current entropy (uncertainty)
    entropy = -np.sum(probs * np.log(probs + 1e-12))

    # Theoretical max VoI = current entropy (if action reveals truth perfectly)
    max_voi = float(entropy)

    most_likely = posterior.get("most_likely", "")
    confidence = posterior.get("confidence", 0.5)

    if confidence < 0.55:
        recommendation = "high_value_in_more_data"
        note = "Low confidence — gathering more evidence has high expected value."
    elif most_likely == "transient_noise":
        recommendation = "low_value"
        note = "High confidence in transient — probably not worth heavy enrichment."
    else:
        recommendation = "moderate_value"
        note = "Some uncertainty remains — targeted enrichment may still help."

    return {
        "current_entropy": round(entropy, 4),
        "max_possible_voi": round(max_voi, 4),
        "recommendation": recommendation,
        "note": note,
        "most_likely": most_likely,
        "confidence": confidence
    }


# Optional helper: save/load posterior to JSON for persistence across sessions
def save_posterior_to_file(posterior: Dict[str, Any], filepath: str = "/tmp/centralmind_bayesian_posterior.json"):
    with open(filepath, "w") as f:
        json.dump(posterior, f, indent=2)


def load_posterior_from_file(filepath: str = "/tmp/centralmind_bayesian_posterior.json") -> Optional[Dict[str, Any]]:
    try:
        with open(filepath, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return None