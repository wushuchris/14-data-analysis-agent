# Automated tests

Run `python -m pytest -q` after installing requirements-dev.txt.

- test_schemas.py validates types, timestamps, numeric constraints, coverage, and immutable inputs.
- test_quality.py checks duplicates, readiness gates, missing data, row counts, source preservation, and safe issue output.

Calculation oracles and model/publication tests will follow with their implementation.
No credentials or live model services are required.
