# SLA Lineage Analytics Investigation Workflow

To trace metric discrepancies back to root system incidents without exposing sensitive runtime values or database logs, use the embedded `lineage_metadata` blocks.

## How to Query Trace Metadata

When looking at data rows from your analytics platform, look for the `lineage_trace_id` property:

```json
{
  "metric_name": "sla_breach_incident",
  "lineage_metadata": {
    "lineage_trace_id": "trc-a1b2c3d4e5f6g7h8",
    "source_outage_id": "out-88291-xyz",
    "source_sla_contract_id": "sla-core-availability-2026"
  }
}