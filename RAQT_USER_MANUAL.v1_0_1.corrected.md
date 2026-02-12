**Clarification (project root vs index location):**
`--target-dir/-t` points to the **project directory** (the directory you would normally `cd` into to run Cargo),
i.e., the directory containing `Cargo.toml`. `raqt generate` writes `RAQT.parquet` into that same directory.
Subsequent commands read that `RAQT.parquet` unless you explicitly pass a different parquet path (e.g., via `--raqt`).

# RAQT User Manual

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


Docs for v1.0.1 (corrected alignment set).

**Rust Analyzer Query Tool** — index Rust source code into `RAQT.parquet` using `rust-analyzer` LSP for compiler-accurate semantic definitions and cross-file references.

See also: `docs/USER_MANUALS.md`.

---

## Quick Start

```bash
uv sync

# 1. Set environment variables (one-time)
export RUST_ANALYZER_PATH=/path/to/rust-analyzer
export RUST_ANALYZER_SHA256=$(sha256sum /path/to/rust-analyzer | cut -d' ' -f1)

# 2. Generate index (requires --trusted flag)
uv run raqt generate --full --trusted

# 3. Query definitions
uv run raqt defs --kind fn --format json

# 4. Query references (call graph)
uv run raqt refs --to-def-id "raqt:src/lib.rs:fn:100:200:main"

# 5. Show statistics
uv run raqt stats
```

---

## Safety / Staleness / Trust Gate Policy

RAQT has a stricter security model than other `*qt` tools because `rust-analyzer` can execute build scripts and proc macros.

### Trust Gate

Before generation, RAQT verifies the rust-analyzer binary:

1. **Existence**: Binary must exist at the configured path
2. **Executable**: Binary must have execute permission
3. **SHA256 match**: Actual hash must match the expected (pinned) hash
4. **--trusted flag**: CLI requires explicit `--trusted` to acknowledge the risk

If any check fails, generation is refused with a clear error message.

### Fail-Closed Staleness

Unlike PYQT/RSQT which can auto-refresh their indexes, RAQT uses **fail-closed** staleness:

- `FAIL_ON_STALE = True` — staleness raises `StalenessError` instead of auto-refreshing
- The user must explicitly re-run `raqt generate --trusted` when data is stale
- This prevents silently spawning rust-analyzer (which could execute arbitrary build scripts)

Staleness is proven by **SHA256 content hashes** of all scanned `.rs` files (including `build.rs`), `Cargo.toml`, `Cargo.lock`, `rust-toolchain.toml`, `rust-toolchain`, and `.cargo/config.toml`/`.cargo/config` files.

### Parquet Safety (shared with all `*qt` tools)

- Runtime tripwire (`doxslock.qt_base.parquet_tripwire`)
- Fail-closed freshness verification (`doxslock.qt_base.index_guard`)
- Root binding: parquet files embed `source_dir` kv-metadata; readers require it
- Atomic writes + locks: writers publish via temp+replace under `*.parquet.lock`

---

## Artifact: `RAQT.parquet`

Three row kinds: **def** (definitions), **ref** (references), **config** (Cargo anchors).

### Schema (28 columns)

| Column | Type | Description |
|--------|------|-------------|
| `record_id` | Utf8 | Unique row identifier (UUID) |
| `file_path` | Utf8 | Relative path from source_dir |
| `source_text` | Utf8 | Full file content (UTF-8) |
| `total_lines` | Int64 | Line count of the file |
| `file_mtime` | Utf8 | File modification timestamp |
| `file_size` | Utf8 | File size in bytes |
| `file_content_hash` | Utf8 | SHA256 of file content |
| `generated_at` | Utf8 | ISO 8601 generation timestamp |
| `raqt_version` | Utf8 | Generator version |
| `row_kind` | Utf8 | "def", "ref", or "config" |
| `entity_id` | Utf8 | Stable entity identifier (def rows) |
| `byte_start` | Int64 | Start byte offset in file |
| `byte_end` | Int64 | End byte offset in file |
| `line_start` | Int64 | Start line number |
| `line_end` | Int64 | End line number |
| `col_start` | Int64 | Start column |
| `col_end` | Int64 | End column |
| `symbol_name` | Utf8 | Symbol name (e.g. "main", "Config") |
| `symbol_kind` | Utf8 | Symbol kind (fn, struct, enum, trait, etc.) |
| `canonical_path` | Utf8 | Fully qualified path (e.g. "crate::module::Type") |
| `visibility_json` | Utf8 | Visibility info as JSON |
| `target_file_path` | Utf8 | Ref target file path |
| `target_byte_start` | Int64 | Ref target byte start |
| `target_byte_end` | Int64 | Ref target byte end |
| `def_json` | Utf8 | Definition-specific JSON payload |
| `from_def_id` | Utf8 | Source entity_id (ref rows) |
| `to_def_id` | Utf8 | Target entity_id (ref rows) |
| `ref_json` | Utf8 | Reference-specific JSON payload |

### KV Metadata (embedded in parquet)

| Key | Description |
|-----|-------------|
| `source_dir` | Absolute path to analyzed Rust project |
| `context_id` | SHA256 of build context (deterministic) |
| `build_context_json` | Canonical JSON of build parameters |
| `ra_version` | rust-analyzer version used |
| `raqt_version` | RAQT generator version |
| `generated_at` | ISO 8601 timestamp |
| `proof_globs` | JSON list of file patterns tracked for freshness |

---

## CLI Reference

Global options (must be placed before the subcommand):

- `--target-dir TARGET_DIR` (short: `-t`): directory containing RAQT.parquet (defaults to `[tool.golden-validator-hybrid].default_source_dir`, falling back to repo root)
- `--version`: show version

### `raqt generate`

Generate RAQT.parquet from rust-analyzer semantic analysis.

```bash
uv run raqt generate --full --trusted
```

Options:

- `--full`: force full regeneration
- `--trusted`: **required** — confirms the rust-analyzer binary is trusted
- `-v, --verbose`: verbose output

Environment variables:

- `RUST_ANALYZER_PATH`: path to the rust-analyzer binary
- `RUST_ANALYZER_SHA256`: expected SHA256 hex digest of the binary
- `RAQT_TARGET_TRIPLE`: target triple (default: `x86_64-unknown-linux-gnu`)
- `RUSTFLAGS`: Rust compiler flags

### `raqt defs`

**Note on `--kind` values:**
Examples use the canonical tokens emitted/accepted by RAQT (e.g. `fn`, `struct`, `enum`, `trait`).
Do **not** use humanized forms like `function` unless the CLI explicitly documents them.


Query definition rows (fn, struct, enum, trait, etc.).

```bash
uv run raqt defs --name "parse" --kind fn --format json
```

Options:

- `--name`: filter by symbol_name (substring match)
- `--kind`: filter by symbol_kind (exact match, e.g. fn, struct, enum, trait)
- `--format`: output format (`text` or `json`, default: text)

### `raqt refs`

Query reference rows (cross-file call graph).

```bash
uv run raqt refs --to-def-id "raqt:src/lib.rs:fn:100:200:main" --format json
uv run raqt refs --from-def-id "raqt:src/lib.rs:fn:100:200:main" --format json
```

Options:

- `--to-def-id`: filter by target entity_id (who calls this?)
- `--from-def-id`: filter by source entity_id (what does this call?)
- `--format`: output format (`text` or `json`, default: text)

### `raqt stats`

Show RAQT index statistics.

```bash
uv run raqt stats
```

### `raqt schema`

Show the RAQT.parquet schema (column names and types).

```bash
uv run raqt schema
```

### `raqt rag-index`

Build a FAISS vector index for semantic search over Rust code.

```bash
uv run raqt rag-index RAQT.parquet --output .raqt.faiss
uv run raqt rag-index RAQT.parquet -o .raqt.faiss --symbol-kinds fn struct --chunk-strategy defs-with-refs
```

Options:

- `parquet` (positional): path to RAQT.parquet file
- `--output, -o` (required): output path for FAISS index
- `--symbol-kinds, -k`: filter to specific symbol kinds (e.g. `fn struct enum`)
- `--chunk-strategy`: `defs` (default) or `defs-with-refs`
- `--include-refs / --no-include-refs`: include ref footers in defs-with-refs strategy (default: True)

Chunk strategies:

| Strategy | Description |
|----------|-------------|
| `defs` | Each def row becomes one chunk with its source_text. Default, fastest. |
| `defs-with-refs` | Each def chunk includes "Referenced by: ..." / "Calls: ..." footer. Richer embeddings for call graph context. |

### `raqt rag-search`

Semantic search over indexed Rust code using natural language.

```bash
uv run raqt rag-search "error handling" --index .raqt.faiss --raqt RAQT.parquet
uv run raqt rag-search "configuration parsing" -i .raqt.faiss --raqt RAQT.parquet --top-k 10
```

Options:

- `query` (positional): natural language search query
- `--index, -i` (required): path to FAISS index file
- `--top-k, -k`: number of results (default: 5)
- `--raqt` (required): path to current RAQT.parquet (for staleness verification)

Note: If you omit `--raqt`, the command must fail with an error. RAQT never "skips" staleness verification.

### `raqt chat`

Chat with indexed Rust code using LLM-powered Q&A.

```bash
uv run raqt chat "How does error handling work?" --index .raqt.faiss --raqt RAQT.parquet
uv run raqt chat "What are the main structs?" -i .raqt.faiss --raqt RAQT.parquet --backend stub
```

Options:

- `question` (positional): question about the Rust code
- `--index, -i` (required): path to FAISS index file
- `--backend`: LLM backend — cloud: `anthropic`, `openai`, `groq`, `together`, `openrouter`, `mistral`; local: `ollama`; testing: `stub` (default: anthropic)
- `--model`: model name (uses backend default if not specified)
- `--top-k, -k`: number of context chunks (default: 5)
- `--max-tokens`: max tokens for LLM response (default: 1024)
- `--temperature`: sampling temperature (default: 0.0)
- `--prompt-profile`: `default` or `grounded` (default: default)
- `--system-prompt`: custom system prompt string
- `--system-prompt-file`: read system prompt from a UTF-8 file (mutually exclusive with `--system-prompt`)
- `--format`: output format (`text` or `json`, default: text)
- `--raqt` (required): path to current RAQT.parquet (for staleness verification)

Note: If you omit `--raqt`, the command must fail with an error. RAQT never "skips" staleness verification.

Supported backends:

| Backend | Type | Notes |
|---------|------|-------|
| `anthropic` | Cloud | Default. Requires `ANTHROPIC_API_KEY` |
| `openai` | Cloud | Requires `OPENAI_API_KEY` |
| `groq` | Cloud | Requires `GROQ_API_KEY` |
| `together` | Cloud | Requires `TOGETHER_API_KEY` |
| `openrouter` | Cloud | Requires `OPENROUTER_API_KEY` |
| `mistral` | Cloud | Requires `MISTRAL_API_KEY` |
| `ollama` | Local | Runs locally, no API key needed |
| `stub` | Testing | Deterministic responses for testing/audits |

---

## Python API

### Generate

```python
from pathlib import Path
from doxslock.raqt.cli import generate_full

generate_full(
    source_dir=Path("path/to/rust-project"),
    output_path=Path("RAQT.parquet"),
    ra_path=Path("/usr/local/bin/rust-analyzer"),
    ra_sha256="abc123...",
)
```

### Query

```python
from doxslock.raqt.query import RAQuery

rq = RAQuery("RAQT.parquet")

# Query definitions
fns = rq.defs(kind="fn")
structs = rq.defs(name="Config", kind="struct")

# Query references (call graph)
callers = rq.refs(to_def_id="raqt:src/lib.rs:fn:100:200:main")
callees = rq.refs(from_def_id="raqt:src/lib.rs:fn:100:200:main")

# Statistics
stats = rq.get_stats()
print(f"Files: {stats['file_count']}, Defs: {stats['def_count']}")
```

### RAG (Semantic Search)

```python
from pathlib import Path
from doxslock.raqt.rag import RaqtRag

# Build index
rag = RaqtRag(store_path=Path(".raqt.faiss"))
rag.index(Path("RAQT.parquet"), symbol_kinds=["fn", "struct"])

# Search
results = rag.search("error handling", top_k=5)
for r in results:
    print(f"{r.chunk.title}: {r.score:.3f}")

# Load pre-built index and search
rag2 = RaqtRag(store_path=Path(".raqt.faiss"))
rag2.load(raqt_parquet=Path("RAQT.parquet"))
results = rag2.search("configuration parsing")
```

### Trust Gate

```python
from pathlib import Path
from doxslock.raqt.trust_gate import verify_rust_analyzer

info = verify_rust_analyzer(
    path=Path("/usr/local/bin/rust-analyzer"),
    expected_sha256="abc123...",
)
print(f"Verified: {info.version} at {info.path}")
```

### Build Context

```python
from doxslock.raqt.build_context import BuildContext

ctx = BuildContext(
    target_triple="x86_64-unknown-linux-gnu",
    features_mode="default",
    features=(),
    profile="debug",
    rustflags="",
)
print(f"Context ID: {ctx.context_id}")
```

---

## RAG Chat Workflow

The full RAG workflow for asking questions about Rust code:

```bash
# 1. Generate semantic index (requires trusted RA binary)
export RUST_ANALYZER_PATH=/path/to/rust-analyzer
export RUST_ANALYZER_SHA256=$(sha256sum /path/to/rust-analyzer | cut -d' ' -f1)
uv run raqt generate --full --trusted

# 2. Build FAISS vector index from definitions
uv run raqt rag-index RAQT.parquet --output .raqt.faiss

# 3. Semantic search (no LLM needed)
uv run raqt rag-search "error handling" --index .raqt.faiss --raqt RAQT.parquet

# 4. LLM-powered Q&A
uv run raqt chat "How does error handling work in this codebase?" \
  --index .raqt.faiss --raqt RAQT.parquet --backend anthropic
```

### Staleness Protection (Full Chain)

The RAG commands enforce a full freshness chain:

```
source .rs files  ──SHA256──▶  RAQT.parquet  ──fingerprint──▶  .raqt.faiss
```

- `rag-index` verifies RAQT.parquet is fresh (vs source files), stores fingerprint in `.meta`
- `rag-search` / `chat` verify FAISS index fingerprint matches current RAQT.parquet
- Any staleness in the chain → `StalenessError` (fail-closed, never auto-refreshes)

---

## rust-xref Integration

**Scope note:**
The `rust-xref` integration below is optional. When running the standalone RAQT tool-audit prompt,
treat this section as **out of scope** unless the user explicitly asks to test integration behavior.


RAQT can be used as an opt-in data source for `rust-xref` cross-reference analysis.

```bash
# Use RAQT for compiler-accurate defs/refs within rust-xref
uv run rust-xref --raqt RAQT.parquet raqt-defs --name "Config"
uv run rust-xref --raqt RAQT.parquet raqt-refs --to-def-id "some_entity_id"
uv run rust-xref --raqt RAQT.parquet raqt-stats
```

The `--raqt` global flag enables 3 subcommands: `raqt-defs`, `raqt-refs`, `raqt-stats`.

> **Note**: RAQT RAG commands (`rag-index`, `rag-search`, `chat`) are standalone RAQT commands only. They are not wired into rust-xref.

---

## RAQT vs RSQT

| Aspect | RAQT | RSQT |
|--------|------|------|
| **Method** | rust-analyzer LSP (compiler-accurate) | Regex-based scanning |
| **Artifact** | `RAQT.parquet` | `RSQT.parquet` |
| **Row kinds** | def, ref, config | One row per entity |
| **Cross-file refs** | Yes (call graph via ref rows) | No |
| **Canonical paths** | Yes (fully qualified) | No |
| **External binary** | rust-analyzer (trust-gated) | None |
| **Staleness** | Fail-closed (`FAIL_ON_STALE = True`) | Auto-refresh available |
| **Speed** | Slower (LSP startup, full analysis) | Fast (regex scan) |
| **Columns** | 28 | 44 |
| **Use case** | Semantic analysis, call graphs, RAG | Safety surfaces, unsafe/FFI/panic audit |

**When to use which**:

- Use **RSQT** for: unsafe code audit, FFI surface discovery, unwrap/panic detection, doc comment extraction
- Use **RAQT** for: call graph analysis, cross-file references, semantic search, type-accurate definitions
- Use **both with rust-xref** for: comprehensive code↔docs cross-reference analysis

---

## Troubleshooting

### "rust-analyzer binary not found"

```bash
export RUST_ANALYZER_PATH=/path/to/rust-analyzer
```

### "SHA256 mismatch for rust-analyzer binary"

The binary at the configured path doesn't match the expected hash. Recompute:

```bash
export RUST_ANALYZER_SHA256=$(sha256sum /path/to/rust-analyzer | cut -d' ' -f1)
```

### "RAQT generate requires --trusted flag"

Intentional safety mechanism. Add `--trusted` to confirm you trust the RA binary:

```bash
uv run raqt generate --full --trusted
```

### "RAQT.parquet not found"

Generate it first, or pass `--target-dir` to specify a different directory:

```bash
uv run raqt generate --full --trusted
```

### `StalenessError` / "index is stale"

Source files changed since last generation. Regenerate:

```bash
uv run raqt generate --full --trusted
```

### "FAISS index not found" (RAG commands)

Build the index first:

```bash
uv run raqt rag-index RAQT.parquet --output .raqt.faiss
```

### RAG search returns no results

- Check that definitions exist: `uv run raqt stats`
- Rebuild the index: `uv run raqt rag-index RAQT.parquet -o .raqt.faiss`
- Try broader queries (fewer symbol_kinds filters)

---

## Module Statistics

| Component | Lines | Description |
|-----------|-------|-------------|
| `cli.py` | 827 | CLI argument parsing, generation, and command dispatch |
| `lsp_client.py` | 254 | rust-analyzer LSP communication |
| `rag_chunks.py` | 182 | Chunk conversion (defs, defs-with-refs strategies) |
| `collector_defs.py` | 168 | Definition collection from RA |
| `rag.py` | 162 | RAG wrapper (fail-closed, def-based chunking) |
| `collector_refs.py` | 128 | Reference collection from RA |
| `query.py` | 104 | Query interface with freshness verification |
| `trust_gate.py` | 96 | SHA256 binary verification |
| `spec.py` | 67 | Schema, constants, and proof globs |
| `columns.py` | 55 | Column name registry (`Col` class) |
| `build_context.py` | 54 | Deterministic build configuration |
| `span_map.py` | 51 | Byte/line span mapping |
| `__init__.py` | 16 | Public exports (`DEFAULT_PARQUET`, `RAQT_VERSION`, `Col`) |
| **Total** | **2,164** | |

**Test coverage**: `tests/raqt/` — 63 tests (1 skip for RA binary dependency) + `tests/crossref/` — 8 rust-xref integration tests
