# Aruba Central Alert Hypotheses & Evidence Mapping

This reference helps you build high-quality `supports` vectors when parsing central-mind output.

## Recommended Hypothesis Space (start here, extend per incident)
- `hardware_issue` — AP/gateway hardware fault, power, overheating, reboot without config change
- `config_drift` — Recent template/push/config change, mismatch between intended and running state
- `transient_noise` — One-off flapping, client density spike that self-resolved, monitoring glitch
- `wlan_density` / `client_density` — High client load, channel utilization, interference, airtime contention
- `software_bug` — Firmware defect, known issue in current AOS-CX / Instant version, controller bug
- `cluster_issue` — Gateway cluster split-brain, master election, tunnel problems (for gateway/cluster alerts)
- `security_event` — DoS, rogue AP, auth failures, Easy Connect issues, certificate problems

## Example Evidence Construction from Common Central Alerts

### AP Rebooted (System category)
supports example:
```json
{
  "hardware_issue": 0.75,
  "software_bug": 0.45,
  "config_drift": 0.20,
  "transient_noise": 0.10
}