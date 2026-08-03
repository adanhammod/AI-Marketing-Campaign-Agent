# Architecture Contract Index

These contracts freeze the Week 1 architecture boundary. If `docs/spec.md` or `docs/plan.md` conflicts with a contract below, correct the summary; do not silently change the contract.

- [`campaign-lifecycle.md`](campaign-lifecycle.md): states, transitions, cancellation, progress, and events.
- [`data-model.md`](data-model.md): typed state, immutable versions, regeneration, DynamoDB entities, leases, and checkpoints.
- [`sqs-message.md`](sqs-message.md): queue message, validation, idempotency, visibility, retry, and DLQ.
- [`api-contracts.md`](api-contracts.md): MVP HTTP request/response and precondition contracts.
- [`artifact-and-error-schemas.md`](artifact-and-error-schemas.md): artifacts, public projections, errors, and redaction.
- [`fixtures.md`](fixtures.md): language-neutral examples.
- [`generated/`](generated/): reproducible JSON Schema; never edit these files manually.

## Shared Package

Location: `shared/src/campaign_contracts/`. It is the authoritative executable definition used by future FastAPI, worker, and test code and intentionally has no FastAPI, boto3, LangGraph, or React dependency.

- Executable fixtures: `shared/fixtures/valid/` and `shared/fixtures/invalid/`.
- Contract tests: `shared/tests/`.
- Generate schemas: `cd shared && python -m campaign_contracts.schema_generation` after installing the package, or `PYTHONPATH=src python -m campaign_contracts.schema_generation`.
- Test with coverage: `cd shared && python -m pytest -q --cov=campaign_contracts --cov-report=term-missing`.
- Install: `python -m pip install shared/` from the repository root.

## Ownership and Change Control

Architecture owns lifecycle and cross-service wire contracts. The service implementing a model may propose a change but cannot change its wire representation independently. Generated schema files are owned by the generator.

Every future contract change must update, in one reviewed change:

1. The Markdown contract.
2. The typed model.
3. The generated JSON Schema.
4. At least one valid or invalid executable fixture.
5. The contract test that proves the new behavior.

Breaking changes require a new explicit schema version and compatibility decision. Existing enum values, meanings, keys, and immutable records cannot be repurposed.

The DynamoDB event key remains `EVENT#<zero-padded-sequence>#<event_id>` because ordering is contractually defined by `event_sequence`; timestamps are informational. This resolves the Task 5 shorthand `EVENT#<timestamp>#<id>` in favor of the frozen data-model contract.