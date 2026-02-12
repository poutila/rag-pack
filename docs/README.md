# FCDRAG (Fail-Closed Deterministic Corrective RAG)

Specialist-LLM + deterministic evidence + contract-gated runner.

## Purpose
Provide a repo-grounded map of the reusable FCDRAG framework in `repo root` and link to operational docs.

## Audience
Engineers and operators who run audits/question packs, tune grounding quality, or extend the runner.

## When to read this
Read this first, then jump to [RUNNER_GUIDE.md](RUNNER_GUIDE.md) to operate, [PACK_AUTHORING.md](PACK_AUTHORING.md) to build packs, and [ARCHITECTURE.md](ARCHITECTURE.md) to extend.

## Canonical naming
This repository standardizes on:
- `FCDRAG` = `Fail-Closed Deterministic Corrective RAG`

Topology fit against `RAG_TOPOLOGIES.md`:
- Rule-Based RAG (`RAG_TOPOLOGIES.md:189`)
- Corrective RAG (`RAG_TOPOLOGIES.md:9`)
- Iterative RAG (`RAG_TOPOLOGIES.md:213`)
- Adaptive RAG (`RAG_TOPOLOGIES.md:57`)

## Mission intent
This framework is tuned for mission-critical Rust audit workflows where "looks fine" is not acceptable.
For mission packs (`pack_type` matching `mission`), the runner now enforces fail-closed advice quality gates in addition to response-schema gates:
- all mission questions must run with `advice_mode=llm`
- advice must contain at least two concrete corrective issues when evidence exists
- each issue must include `WHY_IT_MATTERS`, `PATCH_SKETCH`, `TEST_PLAN`, and evidence-backed citations
- praise-only or generic advice is treated as a contract failure
- questions with zero extracted deterministic evidence are aborted immediately (strict evidence-presence gate)

See [RUNNER_GUIDE.md](RUNNER_GUIDE.md) for operational details and failure signals.

## What FCDRAG is
`run_pack.py` is a generic orchestrator that executes pack YAML questions end-to-end:
1. parse pack + runner policy + engine specs
2. run deterministic preflights
3. inject evidence into prompts
4. run chat or deterministic answer mode
5. enforce response/citation/path validation
6. emit run artifacts + optional plugin outputs

Core behavior is in `run_pack.py:2103` and `run_pack.py:2733`.

## Who it is for
- Audit operators running RSQT/RAQT/MDParse packs
- Pack authors defining strict response contracts
- Plugin authors producing deterministic post-run findings/reports
- Teams porting the same architecture to non-Rust domains

## Quickstart links
- FCDRAG deep explainer (what/why/how/challenges): [FCDRAG.md](FCDRAG.md)
- High-level flow: [ARCHITECTURE.md](ARCHITECTURE.md)
- Concepts and failure semantics: [CONCEPTS.md](CONCEPTS.md)
- Data/corpus/chunking: [DATA_MODEL_PARQUET_AND_CHUNKING.md](DATA_MODEL_PARQUET_AND_CHUNKING.md)
- Pack schema and examples: [PACK_AUTHORING.md](PACK_AUTHORING.md)
- Operations and troubleshooting: [RUNNER_GUIDE.md](RUNNER_GUIDE.md)
- Prompt behavior and modes: [PROMPTS.md](PROMPTS.md)
- Extending engines/packs/plugins: [EXTENDING_AND_PORTING.md](EXTENDING_AND_PORTING.md)
- Experiment outcomes and defaults: [RESEARCH_LOG.md](RESEARCH_LOG.md)
- Evidence-consumption proof plan: [EVIDENCE_USAGE_VALIDATION_PLAN.md](EVIDENCE_USAGE_VALIDATION_PLAN.md)
- Terms: [GLOSSARY.md](GLOSSARY.md)

## Return-After-Years Checklist (Reproducibility)
Use this exact sequence when you return to the project:
1. Open `RUN_MANIFEST.json` for a known run directory and capture `pack path`, input hashes, repo commit, and plugin metrics.
2. Open `RUN_LOG.txt` for the same run and confirm `run.start`, `run.prompts.selected`, `question.chat.prepare`, and `question.done` events.
3. Open `REPORT.md` and `GURU_METRICS.json` to compare runner-level pass rate vs guru-level pass rate.
4. Cross-check historical changes in `OPTIMIZATION.md` and [RESEARCH_LOG.md](RESEARCH_LOG.md).
5. Re-run with the same pack/model/prompt files before changing defaults.

Concrete reproducibility anchor from:
`out/RAQT_MISSION_13_strand_opt/RUN_MANIFEST.json:2`
```json
{
  "schema_version": "1.1",
  "run_id": "22f34e50-88a7-4b3b-81d6-410bd44d327d",
  "pack": {
    "pack_type": "rust_audit_raqt_mission",
    "engine": "raqt",
    "version": "1.0.2"
  },
  "tools": { "runner_version": "3.2.0" },
  "outputs": {
    "score_ok": 8,
    "total_questions": 8,
    "plugin_outputs": {
      "rsqt_guru": { "metrics": { "guru_score": 6, "guru_total": 8, "guru_issues": 2 } }
    }
  }
}
```

Concrete run-telemetry anchor from:
`out/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:3`
```text
event=run.start ... engine=raqt | backend=ollama | model=strand-iq4xs:latest
event=run.prompts.selected ... grounding_prompt=...RUST_GURU_GROUNDING.md | analyze_prompt=...RUST_GURU_ANALYZE_ONLY.md
event=question.chat.prepare ... prompt_mode=analyze_only ... strict_response_template=True | schema_retry_attempts=2
event=question.done ... qid=R_BOUNDARY_1 ... schema_issue_count=0
```

## SSOT inventory (file -> purpose)

| File | Purpose |
|---|---|
| `run_pack.py` | Runner core: CLI, orchestration, validation, artifacts, replicate mode |
| `runner_policy.yaml` | Externalized runner defaults, aliases, validation/prompt/evidence policies |
| `engine_specs.yaml` | Engine CLI wiring per backend tool (`rsqt`, `raqt`, `mdparse`) |
| `pack_rust_audit_rsqt_general_v1_6_explicit.yaml` | Main RSQT audit pack |
| `pack_rust_audit_rsqt_extension_4q.yaml` | RSQT extension 3-question pack |
| `pack_rust_audit_raqt.yaml` | RAQT-first pack with RSQT engine overrides in preflight |
| `docs_audit_pack.explicit.yaml` | MDParse docs audit pack |
| `cfg_*_question_validators.yaml` | Question-level deterministic validator rules |
| `cfg_*_finding_rules.yaml` | Deterministic finding/recommendation rules |
| `plugins/base.py` | Plugin interface (`PackPlugin`, `PluginContext`, `PluginOutputs`) |
| `plugins/rsqt_guru.py` | Post-run deterministic findings + guru report/metrics pipeline |
| `prompts/RUST_GURU_SYSTEM.md` | Base system behavior and fail-closed semantics |
| `prompts/RUST_GURU_GROUNDING.md` | Grounding-mode constraints and citation contract |
| `prompts/RUST_GURU_ANALYZE_ONLY.md` | Analyze-only (quote-bypass) constraints |

This inventory is reused throughout this doc set.

## Authoritative reference docs already in repo
These are treated as authoritative and are cross-linked by this doc set:
- `RUN_PACK_USER_MANUAL_v1_0.md`
- `RUN_PACK_CLI_CHEATSHEET.md`
- `AUDIT_PACK_YAML_STRICT_SCHEMA_REFERENCE_v1_0.md`
- `AUDIT_PACK_YAML_EXAMPLES_COOKBOOK_v1_0.md`
- `RAG_TUNING.md`
- `RAG_TUNING_MANUAL_RSQT_MDPARSE_v1_0.md`

## Grounding notes
- This FCDRAG doc set only claims behavior visible in files above.
- `UNKNOWN/NOT FOUND`: exact parquet internal column schemas are not defined by `run_pack.py`; those are owned by engine tools and their manuals.

## Source anchors
- `run_pack.py:2103`
- `run_pack.py:2733`
- `RAG_TOPOLOGIES.md:9`
- `RAG_TOPOLOGIES.md:57`
- `RAG_TOPOLOGIES.md:189`
- `RAG_TOPOLOGIES.md:213`
- `out/RAQT_MISSION_13_strand_opt/RUN_MANIFEST.json:2`
- `out/RAQT_MISSION_13_strand_opt/RUN_LOG.txt:3`
- `runner_policy.yaml:1`
- `engine_specs.yaml:1`
- `plugins/base.py:8`
- `plugins/rsqt_guru.py:2671`
