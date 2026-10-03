# Proposed data contract

Status: design; schemas and validation follow in the next implementation milestone.

All fixtures and demonstration records are synthetic.

## Completed requests

| Field | Meaning |
|---|---|
| request_id | Unique nonempty synthetic request identifier |
| opened_at | UTC timestamp |
| resolved_at | UTC timestamp, not earlier than opened_at |
| category | Routine, Standard, or Complex |
| region | North, Central, or South |

Elapsed resolution hours are calculated from timestamps, not accepted as an independent source field. They measure elapsed calendar time, not working hours.

Report periods use resolution month. Requests opened before the reporting window may be included if resolved within it. No open requests are inferred from this table.

## Daily operations

| Field | Meaning |
|---|---|
| date | UTC calendar date |
| region | North, Central, or South |
| incoming_requests | Nonnegative integer |
| staffed_hours | Nonnegative finite numeric value |

The unique key is (date, region). Missing daily rows stay missing. Missing or zero staffed hours make requests-per-staffed-hour undefined; do not replace the result with zero.

Opening date and region join request observations to workload records. This is an observational association and may be affected by case mix, staffing assignments, and selection of completed requests.

## Quality policy

- Conflicting duplicate identifiers block affected analyses; never silently choose a record.
- Exact duplicates may be explicitly deduplicated with an audit notice and counts.
- Invalid timestamps or negative durations block affected calculations.
- Missing classifications are reported; grouped findings disclose exclusions and coverage.
- Missing workload records withhold affected workload calculations.
- Tiny groups receive a warning; the exact threshold will be explicit configuration.
- Original records are preserved. Transformations and inclusion/exclusion counts accompany results.

Bundled demo periods will be January through March 2026. Inputs must declare their coverage; lack of observations alone does not prove complete collection or zero workload.
