# Data contract

Status: typed schemas and dataset preparation implemented; file parsing and analytical tools follow.

All fixtures and demonstration records are synthetic.

## Completed requests

| Field | Meaning |
|---|---|
| request_id | Unique nonempty synthetic request identifier |
| opened_at | Timezone-aware ISO timestamp, normalized to UTC |
| resolved_at | Timezone-aware ISO timestamp, normalized to UTC, not earlier than opened_at |
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
| staffed_hours | Nonnegative finite numeric value or missing |

The unique key is (date, region). Missing daily rows stay missing. Missing or zero staffed hours make requests-per-staffed-hour undefined; do not replace the result with zero.

Opening date and region join request observations to workload records. This is an observational association and may be affected by case mix, staffing assignments, and selection of completed requests.

## Quality policy

- Conflicting duplicate identifiers block affected analyses; never silently choose a record.
- Exact duplicates may be explicitly deduplicated with an audit notice and counts.
- Invalid timestamps or negative durations block affected calculations.
- Missing classifications are reported; grouped findings disclose exclusions and coverage.
- Missing workload records withhold affected workload calculations.
- Tiny observed category/region groups receive a warning. Default threshold: 5; configurable with QualityPolicy.
- Invalid or conflicting request rows conservatively block all request analysis; invalid or conflicting operations rows block workload analysis. Blocked tables release no prepared records.
- Missing classifications permit overall summaries, with filtered group views and explicit missing counts. If every classification is missing, that grouping is blocked.
- Operations coverage must include each declared date/region. Missing rows, missing/zero staffing, or missing opening-date links block workload analysis.
- Row limits default to 10,000 per table, and declared operations coverage is limited to one year.
- Boolean counts, numeric strings, nonfinite staffing, unknown enums, naive timestamps, and extra fields are rejected. Dates and aware timestamps are explicitly parsed; classifications are not guessed.
- Original records are preserved. Transformations and inclusion/exclusion counts accompany results.

Bundled demo periods will be January through March 2026. Inputs must declare their coverage; lack of observations alone does not prove complete collection or zero workload.
