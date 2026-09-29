# Changelog

All notable changes to ReleaseWitness are documented here.

## [Unreleased]

Changes motivated by the 2026-09 redundancy study
(`audit/redundancy-2026-09-30/RELWIT_REDUNDANCY_REPORT.md`); not released.

- **Changed:** the QA source fingerprint is content-based (`algorithm: content-v2`).
  - HEAD and dirty state are recorded provenance, not identity. Committing only volatile
    evidence, rewording a commit, or committing exactly the content QA verified keeps QA
    valid; any change to release-source bytes still makes it `QA_STALE`.
  - QA records made by 0.2.0 read as stale once and need one rerun.
- **Added:** `relwit gate [--require-clean] [--json]`, which exits 0 only when a passing
  QA record matches the current source. It needs no work items, roster or supervision
  skills, so it can serve as a CI step or a coding-agent hook.
- **Added:** `relwit qa` as an alias of `supervisor qa`.
- **Fixed:** the runtime spool and telemetry directories now ignore themselves in Git, so
  raw diagnostics in an external project are no longer picked up by `git add -A`.
- **Deprecated:** the supervision layer (DAG, dispatch, mailboxes, runner bridge,
  supervisor cycle, autopilot, checkpoints, knowledge skills, usage telemetry), pending an
  owner decision (ADR-0011).

## [0.2.0] - 2026-09-10

Rebrand / identity migration release. ReleaseWitness is the renamed
continuation of UseAgent; no orchestration feature milestone is introduced.

- Renamed the project from UseAgent to **ReleaseWitness**; the short
  developer-facing identity is **RelWit**.
- Repository moved to `thuanlyt/releasewitness` (same repository, history,
  stars, issues/PRs and historical tags/releases preserved; the previous
  `thuanlyt/UseAgent` URL redirects).
- CLI renamed from `useagent` to `relwit`; the Python package/module is now
  `relwit` (`relwit.cli:main`, `python -m relwit`), replacing the former
  `tools`-namespaced entry point.
- Agent Skill renamed from `$useagent` to `$relwit` (and the paired
  orchestrator/context/worker/review/autopilot skills).
- Config file renamed from `useagent.config.json` to `relwit.config.json`.
- Canonical docs hostname is now `https://relwit.thuanlyt.id.vn/`; the former
  `https://useagent.thuanlyt.id.vn/` hostname is retained as a legacy
  compatibility endpoint.
- New generated work-item IDs use the `RW-####` prefix; existing historical
  `UA-####` references (audit snapshots, the OSBlog case study) are
  preserved unchanged.
- Evidence provenance, source-bound QA, review verification, Git release
  durability and provider-neutral telemetry behavior are unchanged.
  Lightweight supervision remains an optional capability, not the product's
  primary identity.

## [0.1.1] - 2026-09-08

Maintenance-only release for a clean public distribution and assurance-first
positioning:

- Removed maintainer runtime history from the distributed source.
- Made runtime `work/` local/generated after `init`.
- Corrected external-project onboarding.
- Repositioned the project around evidence and release assurance.
- Retained lightweight supervision as an optional capability.
- Switched the hero asset to WebP.
- Made clean-checkout CI initialize the runtime scaffold before tests.

## [0.1.0] - 2026-09-07

Initial public release of UseAgent, a file-first multi-agent control plane for coordinating coding agents and humans in one repository.

### Added

- Scoped, dependency-aware work items with durable registry, mailboxes, reports and bounded supervisor cycles.
- Review and evidence gates with typed provenance, report freshness and takeover/failure lineage.
- Bounded, sanitized runner and QA summaries with detailed diagnostics kept in a local runtime spool.
- Source-bound QA, Git release durability checks and argv-first QA execution by default.
- Optional trusted-local shell execution, worker readiness checks and provider-neutral failure classification.
- Supervisor Judgment and Owner Communication contract for recommendations, tradeoffs, escalation and owner overrides.
- Provider-neutral usage telemetry for measured timing, participants, attempts, retries and takeovers; authoritative token usage is recorded only when a runtime supplies it.

### Reliability / Security

- Release and QA decisions are explicit, bounded and tied to the verified source state.
- The supported threat model is trusted-local / trusted-repository. UseAgent is not an OS sandbox, authenticated agent identity system, provider quota manager, mutually-untrusted isolation boundary, autonomous infinite swarm or deployment authority.
- Raw runtime output and telemetry remain local by default; durable evidence is bounded and sanitized.

### Documentation

- Canonical English and Vietnamese README files with matching capability, trust-model and release guidance.
- Practical Codex, Claude Code and Antigravity-style onboarding and conformance documentation.
- Operations, autopilot, release-integrity and OSBlog dogfood case-study documentation.

[0.2.0]: https://github.com/thuanlyt/releasewitness/releases/tag/v0.2.0
[0.1.1]: https://github.com/thuanlyt/UseAgent/releases/tag/v0.1.1
[0.1.0]: https://github.com/thuanlyt/UseAgent/releases/tag/v0.1.0
