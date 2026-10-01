# Functional acceptance

## Phase-one scope

Phase one tests and improves the migration tool through representative real-plugin components; it does not require or claim complete plugin parity across every target. Retain unsupported components in inventory and reports rather than counting them as passes. LSP capabilities are excluded from migration and behavioral acceptance; their presence may still be reported as unsupported. Do not expand development merely to eliminate every source-plugin blocker.

The delivery boundary defers required-model worker tests, source-runtime resource packaging, target upgrades and third-party integrations, semantic rewrites of source pre-render commands, target evaluator adapters, complete lifecycle enforcement, and unavailable external-service connectivity. Preserve exclusive tool restrictions, manual-only requirements, disabled-service controls, and source files. These deferrals do not permit automatic model substitution, permission grants, credential copying, or source execution.

A blocker is an accepted phase-one limitation only when its evidence and affected component are identified. New converter defects remain failures to repair. Stack delivery requires clean exact-head audits and verification of each layer; the original cumulative draft PR remains open until the replacement stack is verified. No automatic merge is authorized.

## Required evidence

Record the source commit, installed plugin path, target CLI version, exact operation, observed result, and remaining limitations. Separate installation, native resource discovery, prompt expansion, and model/tool behavior. A passing automated suite does not replace these checks.

## Acceptance criteria

| ID | Contract | Pass condition |
| --- | --- | --- |
| A01 | Plugin installation | Install the current checkout through Claude's plugin manager; inventory contains exactly usage and migrate, plus collection hooks. |
| A02 | Manual collection | Invoke a harmless source skill with slash syntax; the installed yi hook increments its count once. |
| A03 | Automatic collection | Invoke that skill through Skill; its count increments once without source filtering. |
| A04 | History idempotence | Import those synthetic sessions twice; live counts do not increase. |
| A05 | Statistics | Invoke `/yi:usage`; compare displayed counts and descending order with the local database query. |
| A06 | Selection | Select multiple sources and targets; unknown or ambiguous IDs fail without broadening the selection. |
| A07 | Standalone resources | Discover and migrate a skill directory and Markdown command without a plugin manifest. |
| A08 | Artifact ownership | Generate into a configured isolated root; one Git repository owns all targets, without requiring identity, signatures, staging, or commits. |
| A09 | Updates | Repeat an unchanged migration without file changes; replace selected resources while preserving unrelated components and user modifications. |
| A10 | Installation | Preview without writes, then explicitly install into a separate target HOME; conflicts and symlinks are rejected. |
| A11 | Skill behavior | Invoke each target's migrated skill and obtain a sentinel stored only in its bundled resource. Listing the skill is insufficient. |
| A12 | Command behavior | Invoke a supported migrated command/template and obtain its defined sentinel. Verify unsupported targets and syntax produce explicit blockers. |
| A13 | Plugin composition | Migrate a plugin containing skills, commands, agents, hooks, and MCP; inspect every component result. Test supported native declarations without silently dropping other components. |
| A14 | Execution controls | MCP declarations stay disabled and Codex hooks remain untrusted. No check grants trust or starts migrated services implicitly. |
| A15 | Native checks | Exercise each product `check --native` entry point; distinguish discovery from behavior. Amp authentication requires explicit approval. |
| A16 | Safety | Reject known private-key resources, embedded MCP credentials, escaping paths, and unintended destination permission changes. |
| A17 | Failure reporting | Batch failures emit JSON, stop subsequent writes, and return a nonzero exit status. |
| A18 | Quality gates | Full tests, branch coverage report, Ruff, pinned ty, prek, plugin validation, and exact-head CI succeed. Record skipped native tests explicitly. |

## Fixture boundary

Use task-owned synthetic resources with unique sentinels. Do not execute arbitrary installed third-party plugins. Keep prompts, logs, credentials, target state, and migration output outside the source repository. Model calls may use existing authorized authentication but must not include private source data. Do not copy credentials into artifact Git repositories.

## Result categories

- **Pass:** observed behavior satisfies the criterion.
- **Fail:** observed behavior contradicts the criterion.
- **Blocked:** authentication, signing, runtime availability, or unsupported target behavior prevents the check.
- **Not tested:** no behavioral evidence collected.

Do not turn blocked or untested items into passes. Preserve exact failure evidence and add regressions for confirmed implementation defects.
