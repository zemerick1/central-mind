---
name: central-vlan-configuration
title: Aruba Central VLAN Configuration Type Selection
description: |
  TRIGGERS — call this when you need to create, modify, delete, or configure
  VLANs in Aruba Central, or translate VLAN configs for switches, gateways,
  or APs. This skill enforces the proper selection between routed SVI interface
  VLANs (`/network-config/v1alpha1/vlan-interfaces`), AP-specific Named VLANs
  (`/network-config/v1alpha1/named-vlan`), and Layer 2 database VLANs
  (`/network-config/v1alpha1/layer2-vlan`).
platforms: [central]
tags: [central, vlan, configuration, campus-ap]
tools: [execute_central, search_central]
---

# Aruba Central VLAN Configuration Type Selection

## Objective

This skill establishes a standardized workflow for creating and modifying VLAN configurations within Aruba Central's Configuration Manager API. It ensures developers and operators correctly choose between AP-specific Named VLANs (for `CAMPUS_AP`) and routed Interface VLANs/SVIs (for `SWITCH` or `MOBILITY_GW`) to prevent deployment failures.

## Prerequisites

- Aruba Central platform is reachable and authenticated.
- The target device type or function (e.g., AP, Switch, Gateway) has been determined.
- The VLAN IDs/ranges and optional settings (IP helpers, descriptions, DHCP snooping) have been defined.

## Procedure

### Step 1 — Determine Device Function
Verify the target device function. The Configuration Manager splits VLAN configuration behavior into two main categories:
1. **Wireless Access Points (`CAMPUS_AP`)**: Require **Named VLANs** to map SSID profiles to VLAN names/ranges.
2. **Wired Switches & Gateways (`SWITCH`, `MOBILITY_GW`)**: Require standard Layer 3 **Interface VLANs (SVIs)** or Layer 2 VLAN database definitions.

### Step 2 — Select the Correct API Endpoint and Method
Consult the decision matrix to select the corresponding HTTP method, path, and query parameters.

### Step 3 — Construct the JSON Payload

#### Scenario A: Named VLANs for APs (`CAMPUS_AP`)
- **API Endpoint**: `POST /network-config/v1alpha1/named-vlan/{name}`
- **Query Parameter**: `object-type=SHARED` (for Library level) or `object-type=LOCAL&scope-id={scope-id}&device-function=CAMPUS_AP`
- **Example Payload**:
  ```json
  {
    "name": "Guest-VLAN-Group",
    "description": "Named VLAN group for Guest SSID matching CAMPUS_AP",
    "assignment": "static",
    "vlan": {
      "vlan-alias": "guest",
      "vlan-id-ranges": ["301", "333"]
    }
  }
  ```

#### Scenario B: SVI / Interface VLANs for Switches & Gateways (`SWITCH` / `MOBILITY_GW`)
- **API Endpoint**: `POST /network-config/v1alpha1/vlan-interfaces/{id}`
- **Query Parameter**: `object-type=SHARED` (for Library level) or `object-type=LOCAL&scope-id={scope-id}&device-function=SWITCH`
- **Example Payload**:
  ```json
  {
    "id": 100,
    "enable": true,
    "description": "SVI Interface for Switch Data VLAN",
    "ipv4": {
      "helper-address": [
        "10.82.96.35",
        "10.82.96.45"
      ]
    }
  }
  ```

#### Scenario C: Basic Layer 2 VLAN definition (All Wired devices)
- **API Endpoint**: `POST /network-config/v1alpha1/layer2-vlan/{vlan}`
- **Example Payload**:
  ```json
  {
    "vlan": 100,
    "name": "data",
    "description-alias": "L2 Data VLAN database entry"
  }
  ```

---

## Decision Matrix

| Target Device Function | VLAN Configuration Type | Target Endpoint | Description / Notes |
|---|---|---|---|
| `CAMPUS_AP` | **Named VLAN / Group** | `/network-config/v1alpha1/named-vlan/{name}` | Binds VLAN aliases or ID ranges to AP SSID templates. |
| `SWITCH` | **Interface VLAN (SVI)** | `/network-config/v1alpha1/vlan-interfaces/{id}` | Standard switch virtual interfaces with SVI IP helper / DHCP configurations. |
| `MOBILITY_GW` | **Interface VLAN (SVI)** | `/network-config/v1alpha1/vlan-interfaces/{id}` | Gateway-specific routing interfaces. |
| All Switch/Gateway scopes | **Layer 2 VLAN** | `/network-config/v1alpha1/layer2-vlan/{vlan}` | Registers the VLAN inside the device database (L2 database entry). |

---

## When NOT to use this skill

- **You are not configuring VLANs in Aruba Central** (e.g. configuring VLANs on Mist or configuring ClearPass policies).
- **You are configuring switch port access/trunk member VLAN memberships** (e.g. binding switchports to VLANs). Use the switch port profile config skill instead.

---

## Examples

- "Configure a VLAN on my central APs"
- "Create a guest VLAN group for campus APs"
- "Create a switch interface VLAN 100 with helper IP"
- "Add a Layer 2 VLAN 200 database entry on switches"

---

## Output formatting

Present the chosen configuration approach as a structured Markdown table mapping the requirement to the API path and JSON payload, like so:

1. **Target Endpoint**: `[METHOD] /path/to/endpoint`
2. **Parameters Used**: Path and query parameters.
3. **Payload Structure**: Clear JSON payload block.
4. **Validation Directive**: Verify the target device function fits the endpoint restrictions.
