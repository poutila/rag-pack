# rag-pack

Fail-Closed Deterministic Corrective RAG runner + audit packs.

## Quick start

Prereqs: Python 3.11+ and `uv` (recommended). This repo is runnable as a standalone clone.

```bash
uv run python ./run_pack.py --help
```

Example (RSQT pack):

```bash
uv run python ./run_pack.py \
  --pack ./pack_rust_audit_rsqt_general_set1_v1_0.yaml \
  --parquet ./RSQT.parquet \
  --index ./.rsqt.faiss \
  --backend ollama \
  --model strand-iq4xs:latest \
  --out-dir ./out/run_$(date +%y%m%d_%H%M%S)
```

## Docs

See `docs/README.md`.
