# Required Checks

This repository owns the primary correctness signal for Datus database adapter
packages. Datus-agent nightly consumes this repository as a cross-repository
integration signal, but it does not replace this repository's own required
checks.

The status context names below are GitHub ruleset contracts. Keep workflow names
and job names stable, or update the ruleset and this document in the same change.

## PR Required Checks

- `Title Check / title-check`
- `Python Format Check / format-check`
- `Adapter CI / unit-tests`

PR checks must stay deterministic and avoid database service startup. The shared
impact selector computes transitive workspace dependents, runs their unit tests,
and builds/imports packages whose runtime code or package metadata changed.
Documentation, release tooling, and unit-test-only changes do not select live
database targets.

## Merge Queue Required Checks

- `Adapter CI / unit-tests`
- `Adapter CI / integration-tests`

`Adapter CI / integration-tests` is a stable aggregate gate. Merge queue runs
select only affected Compose and cloud targets from the transitive package graph:

- Runtime package changes select that package and its transitive dependents.
- Integration tests, Compose files, readiness probes, and target definitions
  select only their owning target.
- Version-only and internal lower-bound-only package changes use package smoke
  checks instead of database tests.
- Shared unit and package-smoke infrastructure changes exercise every workspace
  package without starting databases.
- Shared selection, dependency-lock, and common workflow changes expand to all
  relevant targets.

Manual dispatch runs the full sweep. The scheduled Monday run is the weekly full
Compose and cloud safety net.

## Bypass Policy

Bypass should be reserved for CI bootstrap or incident recovery. A bypass merge
should explain the reason in the PR or a follow-up issue, then restore the
required checks as soon as the repository can validate normally again.
