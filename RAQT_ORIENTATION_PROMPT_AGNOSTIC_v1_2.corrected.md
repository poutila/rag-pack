# AI Assistant Orientation: RAQT (Rust Analyzer Query Tool)

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


Internalize semantic-first analysis for Rust via rust-analyzer LSP.

## Overview

**Version**: 1.1
**Last Updated**: 2026-02-10
**Target Audience**: AI assistants (Claude, GPT, etc.) and humans
**Duration**: 3 minutes
**Expected Outcome**: Behavior change: verify trust gate -> generate with consent -> query defs/refs -> rag-chat for Q&A -> only then read code
**Portability**: Works in any Rust repo with Cargo.toml that rust-analyzer can analyze

## Quick Reference

| Command | Purpose | Example |
|---------|---------|---------|
| `generate` | Create RAQT.parquet (spawns rust-analyzer) | `uv run raqt generate --full --trusted` |
| `defs` | Query definition rows | `uv run raqt defs --name Config --kind struct` |
| `refs` | Query reference rows (call graph) | `uv run raqt refs --to-def-id <id>` |
| `stats` | Show index statistics | `uv run raqt stats` |
| `schema` | Print all 28 columns | `uv run raqt schema` |
| `rag-index` | Build FAISS vector index | `uv run raqt rag-index RAQT.parquet -o .raqt.faiss` |
| `rag-search` | Semantic search over code | `uv run raqt rag-search 'error handling' -i .raqt.faiss --raqt RAQT.parquet` |
| `chat` | LLM-powered Q&A | `uv run raqt chat 'How does X work?' -i .raqt.faiss --raqt RAQT.parquet` |

**Global options** (before subcommand): `--target-dir/-t TARGET_DIR`, `--version`

### Critical Rules

| Rule | Why |
|------|-----|
| Never bypass the trust gate | rust-analyzer executes build scripts and proc macros |
| `FAIL_ON_STALE = True` | Stale reads are refused, not silently served |
| `--trusted` flag is explicit consent | Generation will not proceed without it |
| Always `uv run raqt ...` | Dependency consistency |

### RAQT vs RSQT

| Feature | RAQT | RSQT |
|---------|------|------|
| **Method** | LSP semantic analysis | Regex pattern matching |
| **Accuracy** | Compiler-accurate | Heuristic |
| **References** | Cross-file call graph | Not available |
| **Speed** | Slower (spawns RA process) | Fast (pure regex) |
| **Trust gate** | SHA256 pin + `--trusted` | Not needed |
| **Staleness** | `FAIL_ON_STALE = True` | Auto-refresh |
| **Best for** | Call graphs, refactoring, RAG chat | Safety audits (unsafe, FFI, panics) |

## Complete Orientation

```yaml
schema_version: 2

metadata:
  format: "interactive_orientation"
  duration: "3 minutes"
  goal: "Internalize semantic-first analysis for Rust via rust-analyzer LSP"
  target_audience: "AI assistants (Claude, GPT, etc.) and humans"
  expected_outcome: "Behavior change: verify trust gate → generate with consent → query defs/refs → rag-chat for Q&A → only then read code"
  version: "1.1-yaml-agnostic"
  portability: "Works in any Rust repo with Cargo.toml that rust-analyzer can analyze"
  adaptation_required:
    - "Set RUST_ANALYZER_PATH and RUST_ANALYZER_SHA256 environment variables"
    - "Source directory is auto-discovered; use global --target-dir to control RAQT.parquet location"

how_consumed:
  how: "Execute steps sequentially; treat RAQT as your semantic code intelligence lens."
  side_effects_policy: "Assume read-only unless a step explicitly says writes_files: true."
  failure_policy:
    - "If RAQT.parquet is missing: generate with --full --trusted."
    - "If RAQT.parquet is stale: RAQT refuses to serve stale data (FAIL_ON_STALE = True). Re-run generate."
    - "If SHA256 mismatch: recompute hash after rust-analyzer update."

placeholder_legend:
  "<source_dir>": "Root directory of the Rust project (contains Cargo.toml)."
  "<PROJECT_DIR>": "Project directory containing Cargo.toml and RAQT.parquet (passed via global --target-dir)."
  "<entity-id>": "SHA256 entity_id from a definition row."

role: |
  You are an AI assistant that uses compiler-accurate semantic analysis for Rust code.
  Your motivation: RAQT gives you the exact call graph (who calls what, who uses what)
  without guessing from text patterns — definitions and references come from rust-analyzer,
  the same engine that powers IDE features.

context:
  problem: "Assistants guess Rust relationships from text patterns and miss cross-file references."
  solution: "Index the repo via rust-analyzer LSP, then query definitions and references with compiler accuracy."

  critical_rules:
    - rule: "RAQT spawns an external binary (rust-analyzer). Never bypass the trust gate."
    - rule: "RAQT uses FAIL_ON_STALE = True. Stale reads are refused, not silently served."
    - rule: "The --trusted flag is explicit consent. Generation will not proceed without it."
    - rule: "Prefer `uv run raqt ...` when available for dependency consistency."
    - rule: "Use global --target-dir flag when working with non-default index locations."

  value_proposition:
    summary: "RSQT answers 'what exists here?' — RAQT answers 'what connects to what?'"
    rsqt_scope: "Scans text patterns, reports risk surfaces (unsafe, FFI, unwrap). Fast, safe, good for triage."
    raqt_scope: "Cross-file call graph with compiler accuracy. The fundamental value."
    questions_only_raqt_answers:
      - question: "Who calls this function?"
        command: "refs --to-def-id"
        why_regex_cant: "Regex can't resolve which new() you mean"
      - question: "What does this function depend on?"
        command: "refs --from-def-id"
        why_regex_cant: "Regex can't trace cross-crate imports"
      - question: "Is this the same symbol?"
        mechanism: "Deterministic entity_id"
        why_regex_cant: "Engine::new vs Config::new — both called new"
      - question: "What's the canonical path?"
        column: "canonical_path"
        why_regex_cant: "Re-exports, type aliases, trait impls invisible to text scanning"
    analysis_depth_ladder:
      - level: "Read files manually"
        result: "You see text, guess relationships"
      - level: "RSQT (regex)"
        result: "You see risk surfaces, triage by counts"
      - level: "RAQT (rust-analyzer)"
        result: "You see the actual call graph"
    cost_tradeoff: |
      Each level eliminates a class of guessing. RSQT eliminates "which files matter?"
      guessing. RAQT eliminates "what connects to what?" guessing. The cost is real —
      rust-analyzer spawns an external binary, generation takes 30-120 seconds, and
      staleness requires explicit regeneration. That's why both tools exist: RSQT for
      the 80% case (fast safety triage), RAQT for the 20% where you need compiler-
      accurate semantics.

  security_model:
    why_trust_gate_exists: |
      rust-analyzer is not a passive text parser. To provide semantic analysis, it invokes
      the Rust compiler pipeline, which means:
      1. Build scripts (build.rs) run as arbitrary executables — can read/write files, make
         network calls, execute programs.
      2. Proc macros run as compiler plugins — full system access at compile time.
      3. This happens silently during LSP analysis (documentSymbol, find_references).
      Consequence: running rust-analyzer on an untrusted project = remote code execution.
    defense:
      - "SHA256 pin: binary hash verified at runtime before spawning"
      - "--trusted flag: explicit human consent required for each generation"
      - "FAIL_ON_STALE = True: stale data triggers error, never silent re-generation"
    contrast_with_rsqt: |
      RSQT uses regex text scanning (no binary execution) → auto-refresh is safe.
      RAQT spawns rust-analyzer (binary execution + build scripts) → auto-refresh forbidden.

# =============================================================================
# PREREQUISITES
# =============================================================================

prerequisites:
  required_env_vars:
    RUST_ANALYZER_PATH:
      purpose: "Absolute path to the rust-analyzer binary"
      required: true
      example: "/home/user/.cargo/bin/rust-analyzer"
    RUST_ANALYZER_SHA256:
      purpose: "SHA256 hex digest of the binary (trust verification)"
      required: true
      example: "20a06e644b0d9bd2..."
    RAQT_TARGET_TRIPLE:
      purpose: "Target triple for build context"
      required: false
      default: "x86_64-unknown-linux-gnu"
    RUSTFLAGS:
      purpose: "Passed through to build context metadata"
      required: false

  setup_commands:
    get_sha256: "sha256sum $(which rust-analyzer)"
    set_env: |
      export RUST_ANALYZER_PATH=$(which rust-analyzer)
      export RUST_ANALYZER_SHA256=$(sha256sum $RUST_ANALYZER_PATH | cut -d' ' -f1)

# =============================================================================
# SCHEMA REFERENCE (28 Columns)
# =============================================================================

schema:
  total_columns: 28
  row_kinds:
    def: "Definition row — one per symbol (function, struct, enum, trait, impl, const, etc.)"
    ref: "Reference row — one per cross-reference link between definitions"
    config: "Config anchor — one per non-.rs proof file (Cargo.toml, Cargo.lock, build.rs)"

  categories:
    core_identification:
      description: "File metadata and tracking"
      columns:
        - { name: "record_id",        type: "Utf8",  description: "Unique row identifier (UUID)" }
        - { name: "file_path",        type: "Utf8",  description: "Relative path to .rs file" }
        - { name: "source_text",      type: "Utf8",  description: "Full file content" }
        - { name: "total_lines",      type: "Int64", description: "Line count of file" }
        - { name: "file_mtime",       type: "Utf8",  description: "File modification time" }
        - { name: "file_size",        type: "Utf8",  description: "File size in bytes" }
        - { name: "file_content_hash", type: "Utf8", description: "SHA256 of file content (freshness key)" }
        - { name: "generated_at",     type: "Utf8",  description: "ISO 8601 generation timestamp" }
        - { name: "raqt_version",     type: "Utf8",  description: "RAQT version that generated the row" }

    row_discrimination:
      description: "Row type and identity"
      columns:
        - { name: "row_kind",  type: "Utf8", description: "\"def\", \"ref\", or \"config\"" }
        - { name: "entity_id", type: "Utf8", description: "Deterministic SHA256 ID for definitions" }

    byte_line_spans:
      description: "Source location coordinates"
      columns:
        - { name: "byte_start", type: "Int64", description: "Start byte offset in file" }
        - { name: "byte_end",   type: "Int64", description: "End byte offset in file" }
        - { name: "line_start", type: "Int64", description: "Start line number" }
        - { name: "line_end",   type: "Int64", description: "End line number" }
        - { name: "col_start",  type: "Int64", description: "Start column" }
        - { name: "col_end",    type: "Int64", description: "End column" }

    semantic:
      description: "Compiler-accurate information from rust-analyzer"
      columns:
        - { name: "symbol_name",      type: "Utf8",  description: "Symbol name (e.g. main, Config)" }
        - { name: "symbol_kind",      type: "Utf8",  description: "LSP kind (function, struct, enum, trait, etc.)" }
        - { name: "canonical_path",   type: "Utf8",  description: "Fully qualified path" }
        - { name: "visibility_json",  type: "Utf8",  description: "Visibility information as JSON" }
        - { name: "target_file_path", type: "Utf8",  description: "Target file for references" }
        - { name: "target_byte_start", type: "Int64", description: "Target byte start for references" }
        - { name: "target_byte_end",   type: "Int64", description: "Target byte end for references" }

    definition_specific:
      columns:
        - { name: "def_json", type: "Utf8", description: "Additional definition metadata as JSON" }

    reference_specific:
      columns:
        - { name: "from_def_id", type: "Utf8", description: "Source definition's entity_id" }
        - { name: "to_def_id",   type: "Utf8", description: "Target definition's entity_id" }
        - { name: "ref_json",    type: "Utf8", description: "Additional reference metadata as JSON" }

  entity_id_algorithm:
    name: "P0-C2 deterministic content-addressed ID"
    input: "[context_id, file_path, byte_start, byte_end, symbol_kind, symbol_name]"
    method: "SHA256 of JSON array (canonical separators, ensure_ascii=True)"
    property: "Same code + same build context = same entity_id across generations"

  parquet_kv_metadata:
    - "source_dir — absolute path to analyzed Rust project root"
    - "context_id — SHA256 of canonical build context JSON"
    - "build_context_json — deterministic build configuration"
    - "ra_version — rust-analyzer version string"
    - "raqt_version — RAQT version"
    - "generated_at — ISO 8601 timestamp"
    - "proof_globs — JSON array of glob patterns for freshness scanning"

# =============================================================================
# COMMAND REFERENCE (8 Subcommands)
# =============================================================================
#
# Global options (must be placed BEFORE the subcommand):
#   --target-dir, -t TARGET_DIR  Project directory containing Cargo.toml and RAQT.parquet
#   --version                    Show version (raqt 1.0.0)
#

commands:
  generate:
    purpose: "Create RAQT.parquet via rust-analyzer semantic analysis"
    syntax: "uv run raqt generate --full --trusted [-v]"
    flags:
      --full: "Force full regeneration"
      --trusted: "REQUIRED — confirms the RA binary is trusted"
      -v/--verbose: "Enable verbose logging"
    notes: "Requires RUST_ANALYZER_PATH and RUST_ANALYZER_SHA256 env vars (or pass as args)."
    pipeline:
      - "1. Resolve RA binary path + SHA256 from args or env vars"
      - "2. Verify binary via trust gate (SHA256 comparison)"
      - "3. Create BuildContext (target triple, features, profile, rustflags)"
      - "4. Find all .rs files under source_dir"
      - "5. Start rust-analyzer as LSP subprocess"
      - "6. For each .rs file: didOpen → documentSymbol → collect definitions"
      - "7. For each definition: find_references → collect references"
      - "8. Build config anchor rows for Cargo.toml, Cargo.lock, etc."
      - "9. Write RAQT.parquet with embedded kv-metadata"
    examples:
      basic: "uv run raqt generate --full --trusted"
      verbose: "uv run raqt generate --full --trusted -v"
      custom_dir: "uv run raqt -t /path/to/project generate --full --trusted"

  defs:
    purpose: "Query definition rows"
    syntax: "uv run raqt defs [--name NAME] [--kind KIND] [--format {text,json}]"
    flags:
      --name: "Filter by symbol name (substring match)"
      --kind: "Filter by symbol kind (exact match: function, struct, enum, trait, etc.)"
      --format: "Output format (default: text)"
    examples:
      all_defs: "uv run raqt defs"
      by_name: "uv run raqt defs --name Config"
      by_kind: "uv run raqt defs --kind fn"
      json: "uv run raqt defs --name main --format json"
    text_output: "<file_path>  <symbol_kind>  <symbol_name>"

  refs:
    purpose: "Query reference rows (cross-file call graph)"
    syntax: "uv run raqt refs [--to-def-id ID] [--from-def-id ID] [--format {text,json}]"
    flags:
      --to-def-id: "References pointing TO this definition"
      --from-def-id: "References coming FROM this definition"
      --format: "Output format (default: text)"
    examples:
      to_def: "uv run raqt refs --to-def-id <entity-id>"
      from_def: "uv run raqt refs --from-def-id <entity-id>"
      json: "uv run raqt refs --to-def-id <entity-id> --format json"
    text_output: "<file_path>  <symbol_name>  → <to_def_id>"

  stats:
    purpose: "Show index statistics"
    syntax: "uv run raqt stats"
    examples:
      basic: "uv run raqt stats"
      custom_dir: "uv run raqt -t /path/to/project stats"
    returns: "Total rows, file count, definition count, column count, schema metadata"

  schema:
    purpose: "Print all 28 columns with Polars data types"
    syntax: "uv run raqt schema"
    examples:
      basic: "uv run raqt schema"

  rag-index:
    purpose: "Build a FAISS vector index from RAQT.parquet (semantic search)"
    syntax: "uv run raqt rag-index RAQT.parquet --output .raqt.faiss [--symbol-kinds fn struct ...] [--chunk-strategy defs|defs-with-refs]"
    flags:
      parquet: "Path to RAQT.parquet (positional)"
      --output: "Output path for FAISS index (-o)"
      --symbol-kinds: "Filter to specific symbol kinds (e.g., fn struct enum) (-k)"
      --chunk-strategy: "Chunking strategy: defs (default) or defs-with-refs"
      --include-refs/--no-include-refs: "Include ref footers in defs-with-refs strategy (default: true)"
    examples:
      basic: "uv run raqt rag-index RAQT.parquet --output .raqt.faiss"
      only_functions: "uv run raqt rag-index RAQT.parquet --output .raqt.faiss --symbol-kinds fn"
      with_refs: "uv run raqt rag-index RAQT.parquet --output .raqt.faiss --chunk-strategy defs-with-refs"
    security_note: "rag-index calls _read_fresh_parquet() which verifies RAQT.parquet freshness (fail-closed)."

  rag-search:
    purpose: "Semantic search over indexed Rust code"
    syntax: "uv run raqt rag-search <query> --index .raqt.faiss --raqt RAQT.parquet [--top-k N]"
    flags:
      query: "Natural language search query (positional)"
      --index: "Path to FAISS index file (-i)"
      --raqt: "Path to current RAQT.parquet (required for staleness verification)"
      --top-k: "Number of results to return (-k) (default: 5)"
    examples:
      basic: "uv run raqt rag-search 'error handling' --index .raqt.faiss --raqt RAQT.parquet"
      top_10: "uv run raqt rag-search 'configuration parsing' --index .raqt.faiss --raqt RAQT.parquet --top-k 10"

  chat:
    purpose: "Ask questions about Rust code using LLM-powered Q&A"
    syntax: "uv run raqt chat <question> --index .raqt.faiss --raqt RAQT.parquet [--backend <backend>] [--model <model>] [--top-k N] [--format {text,json}]"
    flags:
      question: "Question to ask about the Rust code (positional)"
      --index: "Path to FAISS index file (-i)"
      --raqt: "Path to current RAQT.parquet (required for staleness verification)"
      --backend: "LLM backend (default: anthropic). Cloud: anthropic, openai, groq, together, openrouter, mistral. Local: ollama. Testing: stub."
      --model: "Model name (uses backend default if not specified)"
      --top-k: "Context chunks to retrieve (-k) (default: 5)"
      --max-tokens: "Max tokens for the LLM response (default: 1024)"
      --temperature: "Sampling temperature (default: 0.0)"
      --prompt-profile: "Prompt template profile: default or grounded"
      --system-prompt: "Optional system prompt string"
      --system-prompt-file: "Read system prompt from a UTF-8 text file"
      --format: "Output format: text or json"
    examples:
      anthropic: "uv run raqt chat 'How does error handling work?' --index .raqt.faiss --raqt RAQT.parquet --backend anthropic"
      ollama: "uv run raqt chat 'What is the architecture?' --index .raqt.faiss --raqt RAQT.parquet --backend ollama --model qwen2.5:14b"
      stub: "uv run raqt chat 'What structs exist?' --index .raqt.faiss --raqt RAQT.parquet --backend stub"
    staleness_chain: |
      Full freshness verification: source .rs files → RAQT.parquet → FAISS index
      Both rag-search and chat verify that the FAISS index matches the current RAQT.parquet
      via fingerprint comparison. If stale, you must re-run rag-index.

# =============================================================================
# STALENESS MODEL (Critical Difference from RSQT)
# =============================================================================

staleness:
  policy: "FAIL_ON_STALE = True — refuse stale data, never auto-refresh"

  why_no_auto_refresh: |
    Auto-refresh would silently spawn rust-analyzer, which executes build scripts
    and proc macros. This is a security-sensitive operation that requires explicit
    human consent (--trusted flag).

  comparison:
    rsqt:
      policy: "Auto-refresh (FAIL_ON_STALE = False)"
      behavior: "Edit .rs file → query → silently regenerates → returns fresh results"
      reason: "Regex text scanning is safe, no binary execution"
    mdparse:
      policy: "Auto-refresh (FAIL_ON_STALE = False)"
      behavior: "Edit .md file → query → archives stale parquet → regenerates → returns fresh"
      reason: "Pure Python text scanning, no external binary"
    raqt:
      policy: "Fail-closed (FAIL_ON_STALE = True)"
      behavior: "Edit .rs file → query → StalenessError → 'Re-run: raqt generate --full --trusted'"
      reason: "Spawns rust-analyzer (heavyweight binary, executes build scripts and proc macros)"

  freshness_mechanism:
    how: "Content-hash based via qt_base shared infrastructure"
    proof_globs: ["**/*.rs", "**/Cargo.toml", "**/rust-toolchain.toml", "**/rust-toolchain", "**/.cargo/config.toml", "**/.cargo/config", "**/Cargo.lock"]
    baseline: "file_path → file_content_hash stored in parquet rows"
    current: "SHA256 of file content on disk"
    stale_when: "Any file in proof_globs has content hash mismatch"
    not_stale_when: "touch alone (mtime change without content change)"

  config_anchor_rows:
    purpose: "Satisfy qt_base freshness contract for non-.rs proof files"
    row_kind: "config"
    files: "All non-.rs files matched by proof_globs (Cargo.toml, Cargo.lock, rust-toolchain.toml, rust-toolchain, .cargo/config.toml, .cargo/config)"
    why: "Without anchors, config files appear as 'added' in freshness diff → always stale"

# =============================================================================
# ORIENTATION STEPS
# =============================================================================

steps:
  step_0:
    name: "Verify Environment"
    duration: "10 seconds"
    writes_files: false
    commands:
      tool: "uv run raqt --help"
      trust: "echo $RUST_ANALYZER_PATH && echo ${RUST_ANALYZER_SHA256:0:16}..."
    stop_conditions:
      - "If raqt is not available: uv sync"
      - "If RUST_ANALYZER_PATH is empty: export RUST_ANALYZER_PATH=$(which rust-analyzer)"
      - "If RUST_ANALYZER_SHA256 is empty: export RUST_ANALYZER_SHA256=$(sha256sum $RUST_ANALYZER_PATH | cut -d' ' -f1)"

  step_1:
    name: "Generate Index (Writes Files, Spawns rust-analyzer)"
    duration: "30-120 seconds (depends on project size)"
    writes_files: true
    command: "uv run raqt generate --full --trusted"
    purpose: "Create RAQT.parquet with compiler-accurate definitions and references."
    security_note: "This spawns rust-analyzer, which may execute build.rs and proc macros."
    alternatives:
      custom_dir: "uv run raqt -t <PROJECT_DIR> generate --full --trusted"
    stop_conditions:
      - "SHA256 mismatch: recompute hash (binary may have been updated)"
      - "Missing Cargo.toml: not a valid Rust project root"
      - "Without --trusted: generation refuses to proceed"

  step_2:
    name: "Verify Index & Stats"
    duration: "5 seconds"
    writes_files: false
    commands:
      stats: "uv run raqt stats"
      verify_defs: "uv run raqt defs --kind fn --format json | head -20"
    purpose: "Confirm the index was generated with expected coverage."
    verification:
      - "Check def_count is reasonable (not 0)"
      - "Check file_count matches expected .rs files"
      - "Verify ra_version in schema metadata"

  step_3:
    name: "Query Definitions"
    duration: "10 seconds"
    writes_files: false
    commands:
      all_structs: "uv run raqt defs --kind struct"
      find_main: "uv run raqt defs --name main"
      specific: "uv run raqt defs --name Config --kind struct --format json"
    purpose: "Explore what symbols exist in the codebase with compiler accuracy."
    triage_strategy:
      - "Start with --kind to understand symbol distribution"
      - "Use --name for targeted lookup"
      - "JSON output includes entity_id for reference queries"

  step_4:
    name: "Query References (Call Graph)"
    duration: "10 seconds"
    writes_files: false
    commands:
      who_calls: "uv run raqt refs --to-def-id <entity-id>"
      what_calls: "uv run raqt refs --from-def-id <entity-id>"
    purpose: "Trace cross-file relationships: who calls this function? What does it use?"
    workflow:
      - "1. Find a definition with 'raqt defs --name X --format json'"
      - "2. Copy its entity_id"
      - "3. Query refs: 'raqt refs --to-def-id <id>' (who references X?)"
      - "4. Or: 'raqt refs --from-def-id <id>' (what does X reference?)"

  step_5:
    name: "Staleness Demo"
    duration: "15 seconds"
    writes_files: true
    commands:
      edit_file: "echo '// test' >> <some_file>.rs"
      try_query: "uv run raqt stats"
      expected: "RAQT.parquet is stale (source files changed since generation).\nRe-run: uv run raqt generate --full --trusted"
      regenerate: "uv run raqt generate --full --trusted"
    purpose: "Experience the FAIL_ON_STALE behavior — RAQT never silently serves stale data."
    key_insight: "This is the security boundary: stale detection triggers an error, not silent re-execution of rust-analyzer."

  step_6:
    name: "RAG Chat (Semantic Search + LLM Q&A)"
    duration: "30 seconds"
    writes_files: true
    commands:
      build_index: "uv run raqt rag-index RAQT.parquet --output .raqt.faiss"
      search: "uv run raqt rag-search 'error handling' --index .raqt.faiss --raqt RAQT.parquet"
      chat_stub: "uv run raqt chat 'How does error handling work?' --index .raqt.faiss --raqt RAQT.parquet --backend stub"
      chat_real: "uv run raqt chat 'How does error handling work?' --index .raqt.faiss --raqt RAQT.parquet --backend anthropic"
    purpose: "Ask natural language questions about Rust code and get LLM-powered answers grounded in compiler-accurate definitions."
    key_insight: |
      The full pipeline: source .rs files → RAQT.parquet (via rust-analyzer) → FAISS index (via embeddings) → semantic search → LLM answer.
      Each link in the chain is freshness-verified. The --raqt flag on rag-search and chat is REQUIRED for staleness checking.
    note: "Use --backend stub for deterministic testing. Cloud backends require appropriate API keys."

# =============================================================================
# DECISION TREE
# =============================================================================

decision_tree:
  question: "What do I need right now?"
  branches:
    symbol_lookup:
      trigger: "Where is struct/function/trait X defined?"
      action: "uv run raqt defs --name X [--kind struct]"
      then: "Read the file at the reported line span"

    call_graph:
      trigger: "Who calls function X? What does X use?"
      action: "uv run raqt defs --name X --format json → copy entity_id → uv run raqt refs --to-def-id <id>"
      then: "Review all callers for impact analysis"

    refactoring_impact:
      trigger: "What breaks if I change X?"
      action: "uv run raqt refs --to-def-id <id> --format json"
      then: "Every row is a file+location that uses X and may need updating"

    codebase_overview:
      trigger: "What symbols exist in this project?"
      action: "uv run raqt stats && uv run raqt defs --kind fn"
      then: "Drill into specific symbols"

    precise_vs_heuristic:
      trigger: "Do I need compiler-accurate info or regex-fast scanning?"
      raqt: "Exact definitions, cross-file references, canonical paths"
      rsqt: "Safety surfaces (unsafe/FFI/panic/unwrap), fast regex, no binary needed"
      both: "Use RSQT for safety triage → RAQT for precise call graph of flagged items"

    semantic_search:
      trigger: "Find code related to a concept (e.g., 'error handling', 'authentication')"
      action: "uv run raqt rag-search '<concept>' --index .raqt.faiss --raqt RAQT.parquet --top-k 10"
      then: "Review ranked results by relevance score"

    ask_question:
      trigger: "I want an LLM-generated answer about the code"
      action: "uv run raqt chat '<question>' --index .raqt.faiss --raqt RAQT.parquet"
      then: "LLM answers grounded in compiler-accurate RAQT definitions"

    semantics:
      trigger: "What does this function actually do?"
      action: "Use RAQT to get the precise definition location → read only that region"

# =============================================================================
# RAQT vs RSQT COMPARISON
# =============================================================================

comparison:
  note: "RAQT and RSQT are complementary, not competing tools"
  table:
    - { feature: "Method",     raqt: "LSP semantic analysis",      rsqt: "Regex pattern matching" }
    - { feature: "Source",     raqt: "rust-analyzer binary",       rsqt: "Pure text scanning" }
    - { feature: "Accuracy",   raqt: "Compiler-accurate",          rsqt: "Heuristic" }
    - { feature: "References", raqt: "Cross-file call graph",      rsqt: "Not available" }
    - { feature: "Speed",      raqt: "Slower (spawns RA process)", rsqt: "Fast (pure regex)" }
    - { feature: "Dependency", raqt: "Requires rust-analyzer",     rsqt: "None" }
    - { feature: "Trust gate", raqt: "SHA256 pin + --trusted",     rsqt: "Not needed" }
    - { feature: "Staleness",  raqt: "FAIL_ON_STALE = True",      rsqt: "Auto-refresh" }
    - { feature: "Best for",   raqt: "Call graphs, refactoring, RAG chat", rsqt: "Safety audits (unsafe, FFI, panics)" }
    - { feature: "RAG Chat",   raqt: "rag-index + rag-search + chat",    rsqt: "rag-index + rag-search + chat" }
  workflow: |
    1. RSQT for quick safety triage: uv run rsqt unsafe && uv run rsqt risk-hotspots
    2. RAQT for precise analysis of flagged items: raqt defs → raqt refs
    3. RAG chat for natural language Q&A: raqt rag-index → raqt chat
    4. Read code only after tools have narrowed the target

# =============================================================================
# ADVANCED WORKFLOWS
# =============================================================================

workflows:
  impact_analysis:
    name: "Refactoring Impact Analysis"
    steps:
      - "uv run raqt defs --name TargetStruct --format json  # Get entity_id"
      - "uv run raqt refs --to-def-id <entity-id> --format json  # All references"
      - "# Review each reference location for required changes"
    use_case: "Before renaming, moving, or modifying a public symbol"

  cross_crate_tracing:
    name: "Cross-Crate Call Tracing"
    steps:
      - "uv run raqt defs --name api_function --format json"
      - "uv run raqt refs --to-def-id <id>  # Who calls it across crates?"
      - "uv run raqt refs --from-def-id <id>  # What does it depend on?"
    use_case: "Understanding dependencies across workspace crates"

  combined_rsqt_raqt:
    name: "Safety-First Semantic Analysis"
    steps:
      - "uv run rsqt unsafe  # Find files with unsafe code"
      - "uv run rsqt risk-hotspots --limit 10  # Prioritize"
      - "uv run raqt defs --name <flagged_function> --format json  # Get exact definition"
      - "uv run raqt refs --to-def-id <id>  # Who calls the unsafe function?"
    use_case: "Audit unsafe code with full call graph"

  full_inventory:
    name: "Complete Symbol Inventory"
    steps:
      - "uv run raqt stats  # Overview"
      - "uv run raqt defs --kind struct --format json  # All structs"
      - "uv run raqt defs --kind trait --format json  # All traits"
      - "uv run raqt defs --kind fn --format json  # All functions"
    use_case: "Onboarding to a new codebase"

  rag_chat:
    name: "Semantic Search + LLM Q&A"
    steps:
      - "uv run raqt rag-index RAQT.parquet --output .raqt.faiss  # Build vector index"
      - "uv run raqt rag-search 'error handling' --index .raqt.faiss --raqt RAQT.parquet  # Find relevant code"
      - "uv run raqt chat 'How does error handling work?' --index .raqt.faiss --raqt RAQT.parquet  # LLM-powered answer"
    use_case: "Natural language exploration of the codebase, grounded in compiler-accurate definitions"
    chunk_strategies:
      defs: "Each def row → one chunk with source_text (default, faster indexing)"
      defs_with_refs: "Each def chunk enriched with 'Referenced by: ...' / 'Calls: ...' footer (richer context)"
    staleness_chain: "source .rs files → RAQT.parquet → FAISS index (all fail-closed)"

  rag_filtered:
    name: "Filtered RAG (Symbol Kind Scoping)"
    steps:
      - "uv run raqt rag-index RAQT.parquet --output .raqt-fns.faiss --symbol-kinds fn  # Functions only"
      - "uv run raqt rag-search 'parsing logic' --index .raqt-fns.faiss --raqt RAQT.parquet"
    use_case: "Focus semantic search on specific symbol types (fn, struct, trait, enum)"

  rag_with_refs:
    name: "RAG with Reference Enrichment"
    steps:
      - "uv run raqt rag-index RAQT.parquet --output .raqt.faiss --chunk-strategy defs-with-refs"
      - "uv run raqt chat 'What depends on the Config struct?' --index .raqt.faiss --raqt RAQT.parquet"
    use_case: "When you want the LLM to see not just the definition but also what calls/references it"

# =============================================================================
# RUST-XREF INTEGRATION
# =============================================================================

rust_xref_integration:
  description: "RAQT is available as an opt-in extension to rust-xref"
  flag: "--raqt RAQT.parquet"
  note: "The existing 33 rust-xref commands work without RAQT (just RSQT + mdparse)"

  subcommands:
    raqt-defs:
      purpose: "Query RAQT definitions through rust-xref unified interface"
      syntax: "uv run rust-xref --raqt RAQT.parquet raqt-defs [--name NAME] [--kind KIND]"
      examples:
        by_name: "uv run rust-xref --raqt RAQT.parquet raqt-defs --name Engine --kind struct"
        by_kind: "uv run rust-xref --raqt RAQT.parquet raqt-defs --kind fn"

    raqt-refs:
      purpose: "Query RAQT references through rust-xref"
      syntax: "uv run rust-xref --raqt RAQT.parquet raqt-refs [--to-def-id ID] [--from-def-id ID]"
      examples:
        who_calls: "uv run rust-xref --raqt RAQT.parquet raqt-refs --to-def-id <entity-id>"
        what_uses: "uv run rust-xref --raqt RAQT.parquet raqt-refs --from-def-id <entity-id>"

    raqt-stats:
      purpose: "Show RAQT index statistics"
      syntax: "uv run rust-xref --raqt RAQT.parquet raqt-stats"

  note_rag_not_in_xref: |
    RAG commands (rag-index, rag-search, chat) are available as standalone RAQT commands
    (uv run raqt rag-index / rag-search / chat) but are NOT wired into rust-xref.
    Use the standalone RAQT CLI for RAG chat functionality.

  why_opt_in:
    - "RAQT requires trust gate setup (env vars + --trusted generation)"
    - "Existing 33 rust-xref commands work with RSQT + mdparse alone"
    - "Not every workflow needs compiler-accurate semantics"

  combined_workflow:
    name: "Safety triage (RSQT) → precise call graph (RAQT)"
    steps:
      - "uv run rust-xref unsafe  # Find unsafe hotspots (RSQT, fast)"
      - "uv run rust-xref --raqt RAQT.parquet raqt-defs --name dangerous_fn --kind fn"
      - "# Copy entity_id from output"
      - "uv run rust-xref --raqt RAQT.parquet raqt-refs --to-def-id <entity-id>"

# =============================================================================
# COMMON MISTAKES
# =============================================================================

common_mistakes:
  - mistake: "Forgetting --trusted flag on generate"
    impact: "Generation refuses to proceed (by design)"
    solution: "Always include --trusted: uv run raqt generate --full --trusted"

  - mistake: "Not setting RUST_ANALYZER_PATH and RUST_ANALYZER_SHA256"
    impact: "DoxslockError: Cannot generate RAQT"
    solution: "export RUST_ANALYZER_PATH=$(which rust-analyzer) && export RUST_ANALYZER_SHA256=$(sha256sum $RUST_ANALYZER_PATH | cut -d' ' -f1)"

  - mistake: "Expecting auto-refresh like RSQT"
    impact: "StalenessError instead of results"
    solution: "RAQT uses FAIL_ON_STALE = True. Re-run: uv run raqt generate --full --trusted"

  - mistake: "Using RAQT for safety surface scanning"
    impact: "Overkill — spawns heavyweight rust-analyzer when regex suffices"
    solution: "Use RSQT for unsafe/FFI/panic/unwrap triage, RAQT for precise call graphs"

  - mistake: "Ignoring 'content modified' warnings during generation"
    impact: "Some references may be skipped (partial results)"
    solution: "Wait for file changes to settle, then regenerate. Partial results are still valid."

  - mistake: "Running RAQT on untrusted Rust projects without reviewing build.rs"
    impact: "build.rs and proc macros execute during analysis — potential code execution"
    solution: "Review build.rs and Cargo.toml dependencies before running with --trusted"

  - mistake: "Running rag-search or chat without the --raqt flag"
    impact: "Command should fail (missing required --raqt); no results are produced. If it succeeds, that is a bug."
    solution: "--raqt is required; always pass the path to RAQT.parquet"

  - mistake: "Forgetting to rebuild FAISS index after regenerating RAQT.parquet"
    impact: "StaleIndexError — FAISS fingerprint doesn't match current RAQT.parquet"
    solution: "Re-run rag-index after each generate: uv run raqt rag-index RAQT.parquet --output .raqt.faiss"

  - mistake: "Using --chunk-strategy defs-with-refs without ref rows in the parquet"
    impact: "No enrichment — behaves identically to 'defs' strategy"
    solution: "Verify ref rows exist: uv run raqt stats (check ref_count > 0)"

# =============================================================================
# QUICK REFERENCE
# =============================================================================

quick_reference:
  setup:
    env_vars: |
      export RUST_ANALYZER_PATH=$(which rust-analyzer)
      export RUST_ANALYZER_SHA256=$(sha256sum $RUST_ANALYZER_PATH | cut -d' ' -f1)

  core_commands:
    generate: "raqt generate --full --trusted"
    stats: "raqt stats"
    schema: "raqt schema"
    defs_all: "raqt defs"
    defs_filtered: "raqt defs --name X --kind struct --format json"
    refs_to: "raqt refs --to-def-id <id>"
    refs_from: "raqt refs --from-def-id <id>"
    rag_index: "raqt rag-index RAQT.parquet --output .raqt.faiss"
    rag_search: "raqt rag-search '<query>' --index .raqt.faiss --raqt RAQT.parquet"
    chat: "raqt chat '<question>' --index .raqt.faiss --raqt RAQT.parquet"

  common_workflows:
    find_symbol: "raqt defs --name X --format json"
    call_graph: "raqt defs --name X --format json → copy entity_id → raqt refs --to-def-id <id>"
    impact: "raqt refs --to-def-id <id> --format json"
    overview: "raqt stats && raqt defs --kind fn"
    rag_chat: "raqt generate → raqt rag-index → raqt chat"
    semantic_search: "raqt rag-search 'error handling' --index .raqt.faiss --raqt RAQT.parquet"

  target_dir_override:
    note: "Use global --target-dir (before subcommand) to specify RAQT.parquet location"
    example: "uv run raqt -t /path/to/project defs --name main"

# =============================================================================
# KEY TAKEAWAYS
# =============================================================================

key_takeaways:
  - "RAQT gives you compiler-accurate definitions and cross-file references via rust-analyzer LSP."
  - "Trust gate is non-negotiable: SHA256 pin + --trusted flag before any generation."
  - "FAIL_ON_STALE = True: stale data is refused, not silently served. This is a security feature."
  - "28 columns: 9 core + 2 row discrimination + 6 spans + 7 semantic + 1 def + 3 ref."
  - "Entity IDs are deterministic (SHA256 of context+file+span+kind+name) — same code = same ID."
  - "Use RSQT for safety triage (fast, no binary), RAQT for precise semantics (accurate, needs RA)."
  - "The workflow: defs → get entity_id → refs (to/from) → read only the relevant code."
  - "Config anchor rows ensure Cargo.toml/Cargo.lock changes are detected by freshness system."
  - "RAG chat: rag-index builds FAISS from def source_text → rag-search for semantic lookup → chat for LLM Q&A."
  - "Full freshness chain: source .rs files → RAQT.parquet → FAISS index. All fail-closed."
  - "8 subcommands: generate, defs, refs, stats, schema, rag-index, rag-search, chat. Global options: --target-dir, --version."
```

## Quick Start

```bash
# 1. Set environment variables (one-time)
export RUST_ANALYZER_PATH=$(which rust-analyzer)
export RUST_ANALYZER_SHA256=$(sha256sum $RUST_ANALYZER_PATH | cut -d' ' -f1)

# 2. Generate index (requires --trusted flag)
uv run raqt generate --full --trusted

# 3. Query definitions
uv run raqt defs --kind fn --format json

# 4. Query references (call graph)
uv run raqt refs --to-def-id <entity-id>

# 5. RAG chat
uv run raqt rag-index RAQT.parquet --output .raqt.faiss
uv run raqt chat 'How does error handling work?' --index .raqt.faiss --raqt RAQT.parquet
```

## Decision Flowchart

```
START: What do I need?
|
+-> Where is symbol X defined?
|   -> raqt defs --name X
|
+-> Who calls function X?
|   -> raqt defs --name X --format json (get entity_id)
|   -> raqt refs --to-def-id <id>
|
+-> What breaks if I change X?
|   -> raqt refs --to-def-id <id> --format json
|
+-> Find code related to a concept?
|   -> raqt rag-search '<concept>' --index .raqt.faiss --raqt RAQT.parquet
|
+-> LLM-powered answer about the code?
|   -> raqt chat '<question>' --index .raqt.faiss --raqt RAQT.parquet
|
+-> Safety audit (unsafe/FFI/panic)?
|   -> Use RSQT instead (fast, no binary needed)
|   -> Then RAQT for precise call graph of flagged items
```

## Common Mistakes

| Mistake | Impact | Fix |
|---------|--------|-----|
| Forgetting `--trusted` | Generation refuses | Always include `--trusted` |
| No env vars set | DoxslockError | `export RUST_ANALYZER_PATH=... RUST_ANALYZER_SHA256=...` |
| Expecting auto-refresh | StalenessError | Re-run `generate --full --trusted` |
| Using RAQT for safety scans | Overkill | Use RSQT for unsafe/FFI triage |
| Missing `--raqt` on rag-search/chat | Skips staleness check | Always pass `--raqt RAQT.parquet` |
| Stale FAISS after regenerate | StaleIndexError | Re-run `rag-index` after each `generate` |

---

**Version**: 1.1
**Last Updated**: 2026-02-10
**Commands**: 8 subcommands + 2 global options
**Schema**: 28 columns (3 row kinds: def, ref, config)
**Data Source**: RAQT.parquet (via rust-analyzer LSP)
**Security**: Trust gate (SHA256 pin + `--trusted`) + fail-closed staleness
**Format**: Hybrid Markdown+YAML (orientation prompt with complete reference)
