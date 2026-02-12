# Glossary

## Purpose
Define FCDRAG-specific terms used across this doc set and runner artifacts.

## Audience
Anyone reading runner outputs, pack YAML, or tuning notes.

## When to read this
Use as a reference while reading [ARCHITECTURE.md](ARCHITECTURE.md), [PACK_AUTHORING.md](PACK_AUTHORING.md), and [RUNNER_GUIDE.md](RUNNER_GUIDE.md).

## Terms

| Term | Meaning in this repo |
|---|---|
| FCDRAG | `Fail-Closed Deterministic Corrective RAG`; this repo's canonical architecture term for contract-gated RAG with deterministic evidence and correction loops |
| Pack | YAML file describing questions, response schema, preflights, and validation (for example `pack_*.yaml`) |
| Question | One audit/query unit in `pack.questions[]` with own preflights and modes |
| Preflight | Deterministic engine command executed before chat to extract evidence |
| Evidence block | Rendered preflight output injected into prompt text |
| CITE token | Synthetic anchor `QID_step.json:1` injected with evidence block and usable in citations |
| Grounding mode | Standard prompt path expecting source-grounded retrieval behavior |
| Quote-bypass mode | Analyze-only path where deterministic evidence is authoritative |
| Analyze-only | Prompt policy that forbids `NOT FOUND` when evidence exists |
| Response schema | Pack-provided output contract text (for example required `VERDICT` and `CITATIONS` lines) |
| VERDICT | Contract field representing outcome class (`TRUE_POSITIVE`, `FALSE_POSITIVE`, `INDETERMINATE` by default) |
| CITATIONS | Contract field containing comma-separated `path:line(-line)` tokens |
| Citation provenance | Check that answer citation tokens appear in injected evidence |
| Path Gate A | `enforce_no_new_paths`: disallow new paths not present in evidence |
| Path Gate B | `enforce_paths_must_be_cited`: body-mentioned paths must appear in `CITATIONS` |
| Deterministic answer mode | `answer_mode=deterministic`; model answer generation skipped |
| Advice mode | Optional second LLM pass (`advice_mode=llm`) for implementation guidance |
| Adaptive top-k | Retry strategy: rerun at max `top_k` after validator issues at lower initial `top_k` |
| Render mode | Evidence formatting mode (`list`, `block`, `lines`, `json`) |
| Transform | Preflight post-processing rules (filters, limits, cross-preflight narrowing) |
| `group_by_path_top_n` | Transform that keeps detail rows only for top paths from another aggregate preflight |
| Runner policy | External YAML defaults/aliases merged onto built-in policy (`runner_policy.yaml`) |
| Engine spec | CLI wiring definition per engine tool in `engine_specs.yaml` |
| Plugin | Post-run extension implementing `PackPlugin` hooks |
| `rsqt_guru` | Built-in plugin producing deterministic findings and guru report/metrics |
| `REPORT.md` | Human-readable run report with answers and validator issues |
| `RUN_MANIFEST.json` | Machine-readable run manifest (inputs, hashes, outputs, plugin metadata) |
| Replicate mode | Multi-seed repeated runs for stability measurement |
| Fail-closed | Safety posture where missing/invalid grounding causes explicit failure, not silent pass |
| `NOT FOUND` | Grounding-mode missing-evidence outcome |
| `INSUFFICIENT EVIDENCE` | Analyze-only insufficient-evidence outcome when evidence exists but is incomplete |

## Related docs
- FCDRAG explainer: [FCDRAG.md](FCDRAG.md)
- Concepts: [CONCEPTS.md](CONCEPTS.md)
- Architecture: [ARCHITECTURE.md](ARCHITECTURE.md)
- Runner operations: [RUNNER_GUIDE.md](RUNNER_GUIDE.md)

## Source anchors
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TOPOLOGIES.md:9`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TOPOLOGIES.md:189`
- `XREF_WORKFLOW_II_new/tools/rag_packs/RAG_TOPOLOGIES.md:213`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:501`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:1481`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:1801`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:1912`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:1968`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2370`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2460`
- `XREF_WORKFLOW_II_new/tools/rag_packs/run_pack.py:2664`
- `XREF_WORKFLOW_II_new/tools/rag_packs/plugins/base.py:26`
- `XREF_WORKFLOW_II_new/tools/rag_packs/plugins/rsqt_guru.py:2922`
- `XREF_WORKFLOW_II_new/tools/rag_packs/runner_policy.yaml:56`
- `XREF_WORKFLOW_II_new/tools/rag_packs/runner_policy.yaml:126`
