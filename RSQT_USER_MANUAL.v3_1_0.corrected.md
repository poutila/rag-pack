# RSQT User Manual

> Canonical architecture term in this repository: **FCDRAG (Fail-Closed Deterministic Corrective RAG)**.


Docs for RSQT v3.1.0 (Track B).

**Rust Source Query Tool** - Index and query Rust source files for safety analysis.

## Overview

RSQT indexes `*.rs` files into a Parquet database (`RSQT.parquet`) enabling fast queries for:

- **Unsafe surface** - `unsafe` blocks, functions, impls, traits
- **FFI surface** - `extern "C"`, `#[no_mangle]`, `#[repr(C)]`, `static mut`
- **Transmute surface** - `mem::transmute` usage (CRITICAL safety audit)
- **Raw pointer surface** - `*const`/`*mut` types (safety review)
- **Runtime failure surface** - `panic!`, `.unwrap()`, `.expect()`
- **Public API surface** - `pub fn`, `pub struct`, `pub trait`
- **Test coverage** - Files with `#[test]` or `#[cfg(test)]`
- **Semantic search** - Natural language queries over code (RAG)
- **LLM chat** - Ask questions about your codebase

## Quick Start

```bash
# Generate index from current directory
uv run rsqt generate --full

# Show statistics
uv run rsqt stats

# Find files with unsafe code
uv run rsqt unsafe

# Find files with FFI surface
uv run rsqt ffi

# Find files with transmute usage (CRITICAL safety audit)
uv run rsqt transmute

# Find files with raw pointer usage
uv run rsqt raw-ptrs

# Find files with panic! usage (reliability audit)
uv run rsqt panics

# Find files with/without tests (test coverage audit)
uv run rsqt test-coverage

# Show module type distribution (lib/bin/mod/test)
uv run rsqt modules

# Show public API surface (pub fn/struct/trait counts)
uv run rsqt api-surface

# Show files with impl blocks (complexity indicator)
uv run rsqt impls

# --- Composite Analysis Commands ---

# Get overall codebase health (letter grade A+ to F)
uv run rsqt health

# Find risk hotspots (files with multiple risk factors)
uv run rsqt risk-hotspots --limit 10

# Find untested files with public API (coverage risk)
uv run rsqt coverage-risk

# Search source code
uv run rsqt search "unwrap"

# List available query columns
uv run rsqt columns

# Dump entire index to JSON
uv run rsqt dump --output rsqt_data.json

# Extract documentation structure (module //! and entity /// docs)
uv run rsqt docs --format json

# --- Audit & Findings ---

# Run unified safety/supply-chain/documentation audit (FINDINGS.jsonl output)
uv run rsqt audit

# Run documentation-derived findings only
uv run rsqt doc-findings

# Filter audit findings by severity
uv run rsqt audit --min-severity HIGH

# Filter audit findings by rule prefix
uv run rsqt audit --rule-prefix DOC_

# --- Semantic Search & Chat (RAG) ---

# Build semantic search index (default chunk strategy: hybrid)
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss

# Natural language search
uv run rsqt rag-search "error handling" --index .rsqt.faiss --rsqt RSQT.parquet

# Chat with your codebase (requires LLM backend)
uv run rsqt chat "How does error handling work?" --index .rsqt.faiss --rsqt RSQT.parquet --backend ollama

# Override the model (optional)
uv run rsqt chat "How does error handling work?" --index .rsqt.faiss --rsqt RSQT.parquet --backend ollama --model llama3.2:3b
```

## Installation

RSQT is part of the doxslock package. Ensure it's installed:

```bash
uv sync
```

Verify the CLI is available:

```bash
uv run rsqt --help
```

## Global Options

| Flag | Short | Description |
|------|-------|-------------|
| `--target-dir` | `-t` | Directory containing RSQT.parquet (default: repo root) |
| `--fail-on-stale` | | Raise error on stale index instead of auto-regenerating. Useful for CI/CD pipelines. |
| `--version` | | Show version |

## Processing JSON Output

Many commands support `--format json` for machine-readable output. Use `jq` for processing:

```bash
# Count entities by kind
uv run rsqt entities --stats --format json | jq '.by_kind'

# Get function names from entities
uv run rsqt entities --kind fn --format json | jq -r '.[].entity_id | split(":") | .[-1]'

# Find files with most unwraps
uv run rsqt prod-unwraps --format json | jq '.results | sort_by(.total) | reverse | .[0:5]'

# Show how many test files were skipped by default
uv run rsqt prod-unwraps --format json | jq '.excluded_test_files'

# Extract file paths with unsafe code
uv run rsqt unsafe --format json | jq -r '.files[].file_path'

# Get column names as JSON array
uv run rsqt columns --format json | jq 'length'  # Count: 44

# Export and filter dump
uv run rsqt dump | jq '.rows | map(select(.has_unsafe == true)) | length'
```

**Note**: Always use `jq` for JSON processing. Never use `python3` directly - use `uv run python` if Python is needed.

## CLI Commands

### `rsqt generate`

Generate or update `RSQT.parquet` index.

```bash
# Full regeneration (recommended for first run)
uv run rsqt generate --full

# Verbose output
uv run rsqt generate --full --verbose
```

The source directory is auto-discovered from the project structure. Output goes to the `--target-dir` directory (default: repo root).

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--full` | | Force full regeneration |
| `--verbose` | `-v` | Show detailed progress |

### `rsqt stats`

Display index statistics.

```bash
uv run rsqt stats
```

**Output:**
```
Files:       42
Total lines: 8,547
Total bytes: 312,456
Safety: snapshot_id=abc123... files=42 verified_at=2026-01-20T09:30:00+00:00
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--include-all-files` | | Include non-.rs proof files (Cargo.toml, etc.) |
| `--format` | | Output format: text (default) or json |

### `rsqt unsafe`

Find files containing unsafe code.

```bash
uv run rsqt unsafe
```

**Output:**
```
Found 3 file(s) with unsafe code:
  src/ffi.rs: 2 block(s), 1 fn(s)
  src/mem.rs: 5 block(s), 0 fn(s)
  src/ptr.rs: 1 block(s), 2 fn(s)
```

### `rsqt ffi`

Find files with FFI/ABI surface.

```bash
uv run rsqt ffi
```

**Output:**
```
Found 2 file(s) with FFI/ABI surface:
  src/bindings.rs: extern_c=5, no_mangle=3, repr_c=2
  src/exports.rs: extern_c=0, no_mangle=8, repr_c=0
```

### `rsqt transmute`

Find files with `mem::transmute` usage (CRITICAL safety audit).

`mem::transmute` is one of the most dangerous operations in Rust - it reinterprets the bits of a value as a different type, bypassing all type system guarantees. Even small mistakes can cause undefined behavior.

```bash
# Text output (default)
uv run rsqt transmute

# JSON output for CI/security pipelines
uv run rsqt transmute --format json
```

**Output (text):**
```
⚠️  CRITICAL: Found 3 transmute call(s) in 2 file(s):
  src/ffi.rs: 2 transmute call(s)
  src/mem.rs: 1 transmute call(s)
```

**Output (json):**
```json
{
  "count": 2,
  "files": [
    {
      "file_path": "src/ffi.rs",
      "transmute_count": 2
    },
    {
      "file_path": "src/mem.rs",
      "transmute_count": 1
    }
  ]
}
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--format` | | Output format: text (default) or json |

**Use cases:**
- Security audits (transmute is a common source of UB)
- CI/CD gates (fail builds with transmute in certain paths)
- Code review prioritization (focus on files with transmute first)

### `rsqt raw-ptrs`

Find files with raw pointer usage (`*const T`, `*mut T`).

Raw pointers bypass Rust's borrow checker and require `unsafe` blocks to dereference. Code with raw pointers needs careful review.

```bash
# Text output (default)
uv run rsqt raw-ptrs

# JSON output for automation
uv run rsqt raw-ptrs --format json
```

**Output (text):**
```
⚠️  Found 5 raw pointer(s) in 2 file(s):
  src/ffi.rs: 3 raw ptr(s)
  src/mem.rs: 2 raw ptr(s)

Raw pointers require unsafe blocks to dereference - review carefully.
```

**Output (json):**
```json
{
  "count": 2,
  "files": [
    {
      "file_path": "src/ffi.rs",
      "raw_ptr_count": 3
    },
    {
      "file_path": "src/mem.rs",
      "raw_ptr_count": 2
    }
  ]
}
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--format` | | Output format: text (default) or json |

**Use cases:**
- Safety audits (raw pointers require unsafe to use)
- FFI boundary analysis (often used with C interop)
- Code review prioritization

### `rsqt panics`

Find files with `panic!` macro usage (reliability audit).

Each `panic!` is a potential crash site. Understanding panic locations helps build more reliable software by converting explicit panics to proper error handling with `Result` or `Option`.

```bash
# Text output (default)
uv run rsqt panics

# JSON output for CI/reliability pipelines
uv run rsqt panics --format json
```

**Output (text):**
```
⚠️  Found 4 panic!(s) in 2 file(s):
  src/parser.rs: 3 panic(s)
  src/validator.rs: 1 panic(s)

Each panic! is a potential crash site - consider Result/Option instead.
```

**Output (json):**
```json
{
  "count": 2,
  "files": [
    {
      "file_path": "src/parser.rs",
      "panic_count": 3
    },
    {
      "file_path": "src/validator.rs",
      "panic_count": 1
    }
  ]
}
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--format` | | Output format: text (default) or json |

**Use cases:**
- Reliability audits (identify crash sites)
- Error handling migration (panic → Result)
- Code quality metrics

### `rsqt test-coverage`

Find files with and without tests (`#[test]` or `#[cfg(test)]`).

Understanding test coverage helps identify untested code that may contain bugs. Files without tests are risk areas.

```bash
# Text output (default)
uv run rsqt test-coverage

# JSON output for CI/test pipelines
uv run rsqt test-coverage --format json

# Show only untested files
uv run rsqt test-coverage --untested-only
```

**Output (text):**
```
Test Coverage: 5/8 files (62.5%)

✅ Tested (5):
  src/lib.rs
  src/parser.rs
  src/validator.rs
  src/config.rs
  src/utils.rs

❌ Untested (3):
  src/legacy.rs
  src/experimental.rs
  src/scratch.rs
```

**Output (json):**
```json
{
  "tested_count": 5,
  "untested_count": 3,
  "total_files": 8,
  "coverage_percent": 62.5,
  "tested_files": [
    {"file_path": "src/lib.rs", "has_tests": true}
  ],
  "untested_files": [
    {"file_path": "src/legacy.rs", "has_tests": false}
  ]
}
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--format` | | Output format: text (default) or json |
| `--untested-only` | | Show only files without tests |

**Use cases:**
- Test coverage audits
- CI/CD quality gates
- Prioritizing test writing efforts

### `rsqt modules`

Show module type distribution (`lib`, `bin`, `mod`, `test`, `module`).

Understanding the module structure helps with codebase navigation and architecture analysis.

```bash
# Text output (default)
uv run rsqt modules

# JSON output
uv run rsqt modules --format json

# Filter by module type
uv run rsqt modules --type lib
```

**Output (text):**
```
Module Distribution (42 files):
  lib: 2
  bin: 1
  mod: 35
  test: 4

Files:
  src/lib.rs (lib)
  src/main.rs (bin)
  src/parser.rs (mod)
  ...
```

**Output (json):**
```json
{
  "distribution": {
    "lib": 2,
    "bin": 1,
    "mod": 35,
    "test": 4
  },
  "total_files": 42,
  "files": [
    {"file_path": "src/lib.rs", "module_type": "lib"},
    {"file_path": "src/main.rs", "module_type": "bin"}
  ]
}
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--format` | | Output format: text (default) or json |
| `--type` | | Filter by module type: lib, bin, mod, test, module |

**Use cases:**
- Codebase structure analysis
- Entry point discovery (lib.rs, main.rs)
- Test file identification

### `rsqt api-surface`

Show public API surface (`pub fn`, `pub struct`, `pub trait` counts).

The public API surface represents the external interface of your crate. Tracking it helps with API stability and documentation coverage.

```bash
# Text output (default)
uv run rsqt api-surface

# JSON output for automation
uv run rsqt api-surface --format json
```

**Output (text):**
```
Public API Surface:
  pub fn: 47
  pub struct: 12
  pub trait: 5
  ─────────────
  Total: 64 items

Top files by API surface:
  src/lib.rs: 15 pub fn, 4 pub struct, 2 pub trait
  src/api.rs: 12 pub fn, 3 pub struct, 0 pub trait
  ...
```

**Output (json):**
```json
{
  "total_pub_fn": 47,
  "total_pub_struct": 12,
  "total_pub_trait": 5,
  "total_api_surface": 64,
  "files": [
    {
      "file_path": "src/lib.rs",
      "pub_fn_count": 15,
      "pub_struct_count": 4,
      "pub_trait_count": 2
    }
  ]
}
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--format` | | Output format: text (default) or json |

**Use cases:**
- API documentation coverage
- Public API size tracking
- Breaking change analysis

### `rsqt impls`

Show files with impl blocks (complexity indicator).

Files with many impl blocks often indicate complex types with multiple trait implementations or substantial method sets. This can help identify refactoring opportunities.

```bash
# Text output (default)
uv run rsqt impls

# JSON output for automation
uv run rsqt impls --format json
```

**Output (text):**
```
Found 42 impl block(s) in 12 file(s):

  src/parser.rs: 8 impl(s)
  src/api.rs: 6 impl(s)
  src/config.rs: 5 impl(s)
  ...

Files with many impls may indicate complex types - review for refactoring opportunities.
```

**Output (json):**
```json
{
  "count": 12,
  "total_impls": 42,
  "files": [
    {
      "file_path": "src/parser.rs",
      "impl_count": 8
    },
    {
      "file_path": "src/api.rs",
      "impl_count": 6
    }
  ]
}
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--format` | | Output format: text (default) or json |

**Use cases:**
- Complexity analysis (many impls = complex type)
- Refactoring planning
- Architecture review

### `rsqt health`

Show overall codebase health dashboard with letter grade.

Combines safety, reliability, and coverage metrics into a single weighted score (0-100) with a letter grade (A+ to F).

**Scoring formula:**
- Safety (35%): Based on absence of unsafe, FFI, transmute
- Reliability (25%): Based on absence of panics and unwraps
- Coverage (40%): Based on percentage of files with tests

```bash
# Text output (default)
uv run rsqt health

# JSON output for dashboards/CI
uv run rsqt health --format json
```

**Output (text):**
```
╔═══════════════════════════════════════════════════╗
║         RSQT Codebase Health Dashboard            ║
╠═══════════════════════════════════════════════════╣
║  Overall Score: 78/100  Grade: B+                 ║
╠═══════════════════════════════════════════════════╣
║  Safety:       85/100   (unsafe: 2, ffi: 1)       ║
║  Reliability:  72/100   (panics: 3, unwraps: 45)  ║
║  Coverage:     75/100   (15/20 files tested)      ║
╚═══════════════════════════════════════════════════╝
```

**Output (json):**
```json
{
  "overall_score": 78.0,
  "grade": "B+",
  "safety": {
    "score": 85.0,
    "unsafe_files": 2,
    "ffi_files": 1,
    "transmute_count": 0
  },
  "reliability": {
    "score": 72.0,
    "panic_count": 3,
    "unwrap_count": 45
  },
  "coverage": {
    "score": 75.0,
    "tested_files": 15,
    "total_files": 20,
    "coverage_percent": 75.0
  },
  "api_surface": {
    "pub_fn_count": 42,
    "pub_struct_count": 12,
    "pub_trait_count": 5
  }
}
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--format` | | Output format: text (default) or json |

**Use cases:**
- CI/CD quality gates (fail if grade < B)
- Project health dashboards
- Progress tracking over time
- Quick codebase assessment

### `rsqt risk-hotspots`

Find files with multiple risk factors, sorted by risk score.

Risk hotspots are files that combine multiple concerning factors: unwraps, panics, transmutes, unsafe code, and untested public APIs.

**Risk score formula:**
`unwraps + (panics × 5) + (transmutes × 20) + unsafe(10) + untested_pub_api`

```bash
# Show top 10 hotspots (default)
uv run rsqt risk-hotspots

# Limit results
uv run rsqt risk-hotspots --limit 5

# JSON output for automation
uv run rsqt risk-hotspots --format json
```

**Output (text):**
```
🔥 Risk Hotspots (Top 10)
═══════════════════════════════════════════════════

1. src/parser.rs (risk: 85)
   unwraps: 12, panics: 3, transmutes: 0, unsafe: yes, untested pub API: 5

2. src/ffi.rs (risk: 72)
   unwraps: 2, panics: 0, transmutes: 2, unsafe: yes, untested pub API: 3

3. src/config.rs (risk: 45)
   unwraps: 8, panics: 0, transmutes: 0, unsafe: no, untested pub API: 7

...
```

**Output (json):**
```json
{
  "hotspots": [
    {
      "file_path": "src/parser.rs",
      "risk_score": 85,
      "factors": {
        "unwrap_count": 12,
        "panic_count": 3,
        "transmute_count": 0,
        "has_unsafe": true,
        "has_tests": false,
        "pub_api_count": 5
      }
    }
  ]
}
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--limit` | `-l` | Limit results (default: 10) |
| `--format` | | Output format: text (default) or json |

**Use cases:**
- Incident response (find likely crash sites)
- Code review prioritization
- Refactoring planning (fix highest-risk files first)
- Security audits

### `rsqt coverage-risk`

Find untested files with public API exposure.

Coverage risk identifies files that lack tests but expose public APIs. These are the highest-risk files for bugs reaching production.

**Exposure score formula:**
`pub_api × 10 + (lines ÷ 100)`

```bash
# Text output (default)
uv run rsqt coverage-risk

# JSON output for automation
uv run rsqt coverage-risk --format json
```

**Output (text):**
```
⚠️  Coverage Risk: 7 untested files with 83 public APIs

  src/api.rs: 120 exposure (12 pub APIs, 340 lines)
  src/handlers.rs: 80 exposure (8 pub APIs, 150 lines)
  src/config.rs: 45 exposure (4 pub APIs, 89 lines)
  ...

Total Exposure Score: 425
Files at risk: 7
```

**Output (json):**
```json
{
  "total_exposure": 425,
  "at_risk_file_count": 7,
  "at_risk_files": [
    {
      "file_path": "src/api.rs",
      "exposure_score": 120,
      "pub_fn_count": 8,
      "pub_struct_count": 3,
      "pub_trait_count": 1,
      "pub_api_count": 12,
      "has_tests": false,
      "line_count": 340
    }
  ]
}
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--format` | | Output format: text (default) or json |

**Use cases:**
- Test prioritization (write tests for highest exposure first)
- Code review focus (untested public API = high risk)
- Quality metrics tracking
- Release readiness assessment

### `rsqt search`

Search source text (case-insensitive substring match).

```bash
# Basic search
uv run rsqt search "unwrap"

# Limit results
uv run rsqt search "unwrap" --limit 5
```

**Output:**
```
src/parser.rs:45: let value = result.unwrap();
src/config.rs:23: config.get("key").unwrap()
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--limit` | `-n` | Maximum results |
| `--include-all-files` | | Search non-.rs proof files too |
| `--format` | | Output format: text (default) or json |

### `rsqt query`

Advanced query with filters.

```bash
# Filter by filename
uv run rsqt query --file lib

# Filter by source content
uv run rsqt query --contains "async fn"

# Select specific columns
uv run rsqt query --columns file_path total_lines has_unsafe

# Combine filters
uv run rsqt query --file parser --contains "Result" --limit 5

# Include entity rows (fn, trait, impl, etc.) alongside file anchors
uv run rsqt query --include-entities --columns file_path entity_kind entity_id --limit 10

# Include all proof files (Cargo.toml, etc.)
uv run rsqt query --include-all-files --columns file_path
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--file` | `-f` | Filter: file path contains |
| `--contains` | `-c` | Filter: source text contains |
| `--columns` | | Columns to return |
| `--limit` | `-n` | Maximum results (default: 20) |
| `--include-all-files` | | Include non-.rs proof files (Cargo.toml, etc.) |
| `--include-entities` | | Include entity rows (fn, trait, impl, etc.) in addition to file anchors |
| `--format` | | Output format: text (default) or json |

### `rsqt entities`

Query extracted entities (Track B v2.0+).

```bash
# Show entity distribution
uv run rsqt entities --stats

# List all entities
uv run rsqt entities

# Filter by kind
uv run rsqt entities --kind fn
uv run rsqt entities --kind trait
uv run rsqt entities --kind impl

# Filter by file
uv run rsqt entities --file parser.rs

# Combine filters
uv run rsqt entities --kind fn --file lib --limit 20

# JSON output
uv run rsqt entities --format json
```

**Output (text):**
```
Entity Distribution:
  fn: 47
  impl: 12
  struct: 8
  trait: 3
  macro: 2
  Total: 72 entities
```

**Output (list):**
```
fn  src/lib.rs:15-22  process_data
fn  src/lib.rs:24-35  validate_input
impl  src/parser.rs:45-89  Parser
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--stats` | | Show entity kind distribution only |
| `--kind` | `-k` | Filter by entity kind (fn, trait, impl, struct, enum, macro, const, static, mod, type) |
| `--file` | `-f` | Filter by file path contains |
| `--limit` | `-n` | Maximum results (default: 50) |
| `--format` | | Output format: text (default) or json |

### `rsqt prod-unwraps`

Find `unwrap()` and `expect()` calls in production code (marker-based heuristic).

```bash
# Show top files with production unwraps
uv run rsqt prod-unwraps

# Include integration tests under **/tests/** (default excludes them)
uv run rsqt prod-unwraps --include-tests

# Limit results
uv run rsqt prod-unwraps --limit 20

# JSON output for CI integration
uv run rsqt prod-unwraps --format json
```

**Output:**
```
Production unwrap/expect calls (top 10):

  src/api.rs: 27 unwrap, 0 expect
  src/orchestrator.rs: 21 unwrap, 0 expect
  src/support_bundle.rs: 20 unwrap, 0 expect

Total files: 10
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--limit` | `-n` | Maximum results (default: 20) |
| `--include-tests` | | Include files under `tests/` directories (default: excluded) |
| `--format` | | Output format: text (default) or json |

**Notes (heuristic):**
- Marker-based scan: counts unwrap/expect **before** the first test boundary marker (`#[cfg(test)]`, `mod tests {`, `#[test]`).
- Path-level exclusion: by default, files under `**/tests/**` are excluded from results to avoid misinterpreting integration tests as “production”.
- Known limitations include false negatives after inline test modules and false positives if markers appear in comments/strings.
- `--format json` includes heuristic warnings for edge cases.

### `rsqt rag-index`

Build a semantic search index from RSQT.parquet for natural language queries.

```bash
# Build FAISS index (hybrid by default: entities + allowlisted file anchors)
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss

# Index only functions
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss --entity-kinds fn

# Index functions and structs
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss --entity-kinds fn struct

# Index only allowlisted file anchors (no entity rows)
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss --chunk-strategy files

# Index entities + allowlisted file anchors (hybrid)
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss --chunk-strategy hybrid
```

**Output:**
```
Loading RSQT.parquet: RSQT.parquet
Indexed 1340 chunks to: .rsqt.faiss
```

If `--chunk-strategy` is `files` or `hybrid`, the output reports `chunks` instead of `entities`.

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `parquet` | | Path to RSQT.parquet file (positional) |
| `--output` | `-o` | Output path for FAISS index (required). Must be a file path (not a directory). |
| `--entity-kinds` | `-k` | Filter to specific entity kinds (e.g., fn struct) |
| `--chunk-strategy` | | Chunk strategy: `hybrid` (default), `entities`, `files` |
| `--include-anchor-glob` | | Additional allowlist glob for file anchors (repeatable) |
| `--exclude-anchor-glob` | | Additional denylist glob for file anchors (repeatable) |
| `--anchor-allowlist-mode` | | Allowlist behavior: `extend` (default+user) or `replace` (user-only) |
| `--anchor-window-lines` | | File-anchor window size in lines (default: 200) |
| `--anchor-overlap-lines` | | File-anchor window overlap in lines (default: 20) |
| `--max-anchor-chars` | | Truncate file-anchor source_text to at most this many characters (default: 20000) |

**Notes:**
- `--chunk-strategy=files` indexes only file anchors and ignores `--entity-kinds`.
- Allowlisted file anchors are matched by file path glob. Defaults:
  - allowlist: `Cargo.toml`, `**/Cargo.toml`, `**/rust-toolchain`, `**/rust-toolchain.toml`, `**/rust-toolchain.*`, `**/.cargo/config`, `**/.cargo/config.toml`, `**/src/main.rs`, `**/src/lib.rs`, `**/build.rs`
  - denylist: `Cargo.lock`, `**/Cargo.lock`, `**/target/**`

### `rsqt rag-search`

Semantic search over indexed Rust code using natural language queries.

```bash
# Search for error handling code
uv run rsqt rag-search "error handling and result types" --index .rsqt.faiss --rsqt RSQT.parquet

# Get more results
uv run rsqt rag-search "configuration parsing" --index .rsqt.faiss --rsqt RSQT.parquet --top-k 10
```

**Output:**
```
Top 5 results for: 'error handling and result types'

1. crates/engine/src/error.rs::fn new_validation_error (score: 0.542)
   Lines 45-52
   pub fn new_validation_error(msg: &str) -> Error { Error::Validation { message: msg.to...

2. crates/engine/src/report.rs::fn strict_ok (score: 0.518)
   Lines 78-85
   pub fn strict_ok(&self) -> bool { self.errors.is_empty() && self.results.iter().all...
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `query` | | Natural language search query (positional) |
| `--index` | `-i` | Path to FAISS index file (required) |
| `--rsqt` | | Path to current RSQT.parquet (required for **Note on staleness messages:**  
Validate staleness behavior by outcomes (refusal on `--fail-on-stale` and automatic refresh when allowed),
not by exact log message text, since messages may change across versions.

staleness verification) |
| `--top-k` | `-k` | Number of results to return (default: 5) |

### `rsqt chat`

Chat with indexed Rust code using LLM-powered Q&A. Combines semantic search with language model reasoning.

```bash
# Ask about the codebase (cloud backend)
uv run rsqt chat "How does error handling work?" --index .rsqt.faiss --rsqt RSQT.parquet --backend anthropic

# Use local Ollama (no API key needed)
uv run rsqt chat "What is the IPC architecture?" --index .rsqt.faiss --rsqt RSQT.parquet --backend ollama

# JSON output for programmatic use
uv run rsqt chat "List all async functions" --index .rsqt.faiss --rsqt RSQT.parquet --backend openai --format json

# Add a backend-specific system prompt (optional)
uv run rsqt chat "Summarize the safety model" --index .rsqt.faiss --rsqt RSQT.parquet \
  --backend ollama \
  --system-prompt "You are a Rust security auditor. Be precise and cite sources."
```

**Output:**
```
Using anthropic backend (model: claude-sonnet-4-5-20250929)

💬 Question: How does error handling work in this codebase?

📖 Answer:
Error handling in this codebase uses a custom Error enum with variants for
validation errors, I/O errors, and internal errors. The Error type implements
std::error::Error and provides context via the `new_validation_error()` and
`new_io_error()` constructors...

📚 Sources (5 chunks used):
   • crates/engine/src/error.rs::impl Error
   • crates/engine/src/report.rs::fn strict_ok
   • crates/engine/tests/strict_mode.rs::fn test_error_propagation

🤖 Model: claude-sonnet-4-5-20250929 | Tokens: 847 | Backend: anthropic
```

**Supported Backends:**

| Backend | Type | Model Default | API Key Env Var |
|---------|------|---------------|-----------------|
| `anthropic` | Cloud | claude-sonnet-4-5-20250929 | `ANTHROPIC_API_KEY` |
| `openai` | Cloud | gpt-4o-mini | `OPENAI_API_KEY` |
| `groq` | Cloud | llama-3.1-70b-versatile | `GROQ_API_KEY` |
| `together` | Cloud | meta-llama/Llama-3.1-70B-Instruct-Turbo | `TOGETHER_API_KEY` |
| `openrouter` | Cloud | meta-llama/llama-3.1-70b-instruct | `OPENROUTER_API_KEY` |
| `mistral` | Cloud | mistral-large-latest | `MISTRAL_API_KEY` |
| `ollama` | Local | hf.co/Fortytwo-Network/Strand-Rust-Coder-14B-v1-GGUF:Q4_K_M (RSQT default) | None (local) |
| `stub` | Deterministic | stub | None |

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `question` | | Question to ask (positional) |
| `--index` | `-i` | Path to FAISS index file (required) |
| `--backend` | | LLM backend (default: anthropic) |
| `--model` | | Model name (uses backend default if not specified). For `--backend ollama`, RSQT uses a Rust-tuned default when `--model` is omitted. |
| `--top-k` | `-k` | Context chunks to retrieve (default: 5) |
| `--max-tokens` | | Max tokens for the LLM response (default: 1024) |
| `--temperature` | | Sampling temperature (default: 0.0) |
| `--prompt-profile` | | Prompt template profile: `default` or `grounded` (default: default) |
| `--system-prompt` | | Optional system prompt string (passed as a system message when supported). Mutually exclusive with `--system-prompt-file`. |
| `--system-prompt-file` | | Read system prompt from a UTF-8 text file (alternative to `--system-prompt`). Mutually exclusive with `--system-prompt`. |
| `--rsqt` | | Path to current RSQT.parquet (required for staleness verification) |
| `--format` | | Output format: text (default) or json |

**Prompt profiles:**
- `default`: behavior-preserving prompt template.
- `grounded`: stricter "no guessing" mode; requires citing retrieved `Section:` entries and uses `NOT FOUND` when the retrieved context lacks the answer.

### `rsqt dump`

Export RSQT.parquet contents to JSON format.

```bash
# Dump to stdout
uv run rsqt dump

# Dump to file
uv run rsqt dump --output rsqt_data.json

# Dump from specific project
uv run rsqt --target-dir /path/to/project dump --output export.json
```

**Output Structure:**
```json
{
  "source": "RSQT.parquet",
  "row_count": 1440,
  "rows": [
    {
      "file_path": "src/lib.rs",
      "entity_kind": "file",
      "has_unsafe": false,
      "total_lines": 150,
      ...
    }
  ]
}
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--output` | `-o` | Output JSON file path (default: stdout) |

**Notes:**
- Includes all rows (file anchors + entities)
- Includes all columns (44 columns in v3.1 schema)
- Uses `default=str` for JSON serialization of non-standard types

### `rsqt columns`

List available columns in RSQT.parquet schema.

```bash
# Text output (one column per line)
uv run rsqt columns

# JSON output (array of column names)
uv run rsqt columns --format json
```

**Output (text):**
```
byte_end
byte_start
doc_comment
entity_doc_comment
entity_id
entity_kind
...
```

**Output (json):**
```json
[
  "byte_end",
  "byte_start",
  "doc_comment",
  ...
]
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--format` | | Output format: text (default) or json |

**Use cases:**
- Discover queryable fields for `rsqt query --columns`
- Verify schema version (v2.x has 40 columns)
- Generate documentation or tooling

### `rsqt docs`

Extract documentation structure (module-level `//!` and entity-level `///` docs) as hierarchical JSON.

```bash
# Text format (default)
uv run rsqt docs

# JSON format (hierarchical by file)
uv run rsqt docs --format json

# Filter by file
uv run rsqt docs --format json --file lib.rs

# Filter by entity kind
uv run rsqt docs --format json --kind fn

# Show only undocumented entities
uv run rsqt docs --format json --missing-only
```

**Output (text):**
```
=== src/lib.rs ===
Module doc: This is the library module documentation.

  fn add
    Doc: Adds two numbers together.

  struct Documented
    Doc: A documented struct.

  fn undocumented
    Doc: (none)
```

**Output (json):**
```json
{
  "files": [
    {
      "file_path": "src/lib.rs",
      "module_doc": "This is the library module documentation.\nIt spans multiple lines.",
      "entities": [
        {
          "entity_id": "add",
          "kind": "fn",
          "line_start": 8,
          "line_end": 10,
          "doc": "Adds two numbers together.\n\n# Examples\n```\nlet result = add(1, 2);\n```"
        },
        {
          "entity_id": "undocumented",
          "kind": "fn",
          "line_start": 18,
          "line_end": 18,
          "doc": null
        }
      ]
    }
  ]
}
```

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--format` | | Output format: text (default) or json |
| `--file` | `-f` | Filter by file path substring |
| `--kind` | `-k` | Filter by entity kind (fn, struct, trait, impl, enum, macro) |
| `--missing-only` | | Show only entities with missing documentation |

**Use cases:**
- Documentation coverage analysis
- API documentation extraction
- Finding undocumented public entities
- Generating documentation reports

### `rsqt audit`

Run unified audit producing FINDINGS.jsonl output with deterministic rules.

Combines safety, supply-chain, and documentation rules into a single audit output format suitable for CI/CD pipelines.

```bash
# Default audit (all rules)
uv run rsqt audit

# Filter by minimum severity
uv run rsqt audit --min-severity HIGH

# Filter by rule prefix
uv run rsqt audit --rule-prefix DOC_

# Combine filters
uv run rsqt audit --min-severity MEDIUM --rule-prefix UNSAFE
```

**Output (JSONL):**
```json
{"finding_id": "abc123...", "rule_id": "UNSAFE_BLOCK_PRESENT", "severity": "HIGH", "status": "OPEN", "confidence": "DETERMINISTIC", "message": "File contains unsafe blocks", "evidence": [{"source": "rsqt_query", "ref": "src/ffi.rs"}]}
{"finding_id": "def456...", "rule_id": "DOC_PUB_MISSING", "severity": "MEDIUM", "status": "OPEN", "confidence": "DETERMINISTIC", "message": "Public function undocumented_pub lacks documentation", "evidence": [{"source": "rsqt_docs", "ref": "src/lib.rs::undocumented_pub"}]}
```

**Rules included:**

| Rule ID | Severity | Condition |
|---------|----------|-----------|
| `UNSAFE_BLOCK_PRESENT` | HIGH | `has_unsafe=true` |
| `FFI_SURFACE_PRESENT` | HIGH | `has_ffi=true` |
| `TRANSMUTE_USAGE` | HIGH/CRITICAL | `transmute_count>0` (CRITICAL if in pub entity) |
| `RAW_PTR_USAGE` | HIGH | `raw_ptr_count>0` |
| `PANIC_IN_PROD` | MEDIUM | `prod_panic_count>0` in non-test files |
| `BUILD_RS_SUPPLY_CHAIN` | HIGH | `file_path` ends with `build.rs` |
| `DOC_PUB_MISSING` | MEDIUM | Public entity without docs |
| `DOC_PUB_FN_MISSING_*` | LOW | Section gaps in documented pub fn |
| `DOC_MODULE_DOC_MISSING` | LOW | File with pub entities, no module doc |
| `DOC_PUB_TYPE_MISSING` | LOW | Undocumented pub struct/enum/trait |

**Options:**
| Flag | Short | Description |
|------|-------|-------------|
| `--min-severity` | | Filter findings by minimum severity (INFO, LOW, MEDIUM, HIGH, CRITICAL) |
| `--rule-prefix` | | Filter findings by rule ID prefix (e.g., DOC_, UNSAFE) |

**Use cases:**
- CI/CD quality gates
- Security audits
- Pre-release compliance checks
- Automated code review

### `rsqt doc-findings`

Run documentation-derived findings producing FINDINGS.jsonl output.

Generates findings specifically from documentation analysis (missing docs, incomplete doc sections).

```bash
# Default doc-findings
uv run rsqt doc-findings
```

**Output (JSONL):**
```json
{"finding_id": "abc123...", "rule_id": "DOC_PUB_MISSING", "severity": "MEDIUM", "status": "OPEN", "confidence": "DETERMINISTIC", "message": "Public function undocumented_pub lacks documentation", "evidence": [{"source": "rsqt_docs", "ref": "src/lib.rs::undocumented_pub"}]}
{"finding_id": "def456...", "rule_id": "DOC_PUB_FN_MISSING_ARGS", "severity": "LOW", "status": "OPEN", "confidence": "DETERMINISTIC", "message": "Function missing_args has parameters but no Arguments section", "evidence": [{"source": "rsqt_docs", "ref": "src/lib.rs::missing_args"}]}
```

**Rules included:**

| Rule ID | Severity | Condition |
|---------|----------|-----------|
| `DOC_PUB_MISSING` | MEDIUM | `visibility=="pub"` AND `!has_doc` |
| `DOC_PUB_FN_MISSING_ARGS` | LOW | pub fn with params, has_doc but no Arguments section |
| `DOC_PUB_FN_MISSING_RETURNS` | LOW | pub fn with return, has_doc but no Returns section |
| `DOC_RESULT_MISSING_ERRORS` | MEDIUM | pub fn returns Result, has_doc but no Errors section |
| `DOC_PUB_FN_MISSING_EXAMPLES` | LOW | pub fn has_doc but no Examples section |
| `DOC_MODULE_DOC_MISSING` | LOW | File has pub entities but no module doc |
| `DOC_PUB_TYPE_MISSING` | LOW | pub struct/enum/trait without docs |

**Use cases:**
- Documentation coverage enforcement
- API documentation quality checks
- Documentation CI gates

## Python API

### Basic Usage

```python
from pathlib import Path
from doxslock.rsqt import RSQuery, generate_full

# Generate index
generate_full(source_dir=Path("/path/to/rust/project"))

# Query the index
q = RSQuery("RSQT.parquet")

# Get statistics
stats = q.get_stats()
print(f"Files: {stats['file_count']}")
print(f"Lines: {stats['total_lines']}")

# Find unsafe files
for row in q.unsafe_files():
    print(f"{row['file_path']}: {row['unsafe_block_count']} blocks")

# Find FFI files
for row in q.ffi_files():
    print(f"{row['file_path']}: {row['no_mangle_count']} exports")
```

### Advanced Queries

```python
from doxslock.rsqt import RSQuery

q = RSQuery("RSQT.parquet")

# Query with filters
results = q.query(
    file="parser",           # file path contains "parser"
    contains="async",        # source contains "async"
    has_unsafe=True,         # has unsafe code
    has_tests=True,          # has test module
    columns=["file_path", "total_lines", "unsafe_block_count"],
    limit=10,
)

# Search source text
hits = q.search("unwrap", limit=20)
for hit in hits:
    print(f"{hit['file_path']}:{hit['line_number']}: {hit['line_text']}")

# Discover available columns
columns = q.available_columns()
print(f"Schema has {len(columns)} columns")
print(f"Has 'has_unsafe': {'has_unsafe' in columns}")

# Sum numeric columns
total_lines = q.sum_column("total_lines")
total_unsafe = q.sum_column("unsafe_block_count")
print(f"Total lines: {total_lines}, Total unsafe blocks: {total_unsafe}")
```

### RSQuery Class Reference

```python
class RSQuery:
    def __init__(
        self,
        parquet_path: Path | str | None = None,
        *,
        fail_on_stale: bool = False,  # If True, raise error instead of auto-refreshing
        verbose: bool = False,
    ) -> None: ...

    @property
    def parquet_path(self) -> Path: ...

    @property
    def source_dir(self) -> Path | None: ...

    @property
    def safety_proof(self) -> SafetyProof | None: ...

    def get_stats(self) -> dict[str, Any]: ...

    def query(
        self,
        *,
        file: str | None = None,        # Filter by file path
        contains: str | None = None,    # Filter by source content
        has_unsafe: bool | None = None, # Filter by unsafe presence
        has_ffi: bool | None = None,    # Filter by FFI presence
        has_tests: bool | None = None,  # Filter by test presence
        columns: list[str] | None = None,
        limit: int | None = None,
        include_all_files: bool = False,  # Include Cargo.toml, etc.
        include_entities: bool = False,   # Include fn, trait, impl, etc.
    ) -> list[dict[str, Any]]: ...

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]: ...

    def unsafe_files(self) -> list[dict[str, Any]]: ...

    def ffi_files(self) -> list[dict[str, Any]]: ...

    # Track B entity methods (v2.0+)
    def entities(
        self,
        *,
        kind: str | None = None,       # Filter by entity kind
        file: str | None = None,       # Filter by file path
        columns: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]: ...

    def entity_stats(self) -> dict[str, int]: ...

    # Utility methods
    def available_columns(self) -> set[str]:
        """Return set of all column names in the parquet file."""
        ...

    def sum_column(self, column: str) -> int:
        """Sum values in a numeric column across all rows."""
        ...

    def has_column(self, column: str) -> bool:
        """Check if a column exists in the parquet file."""
        ...

    def has_entities(self) -> bool:
        """Check if any entity-level rows exist (not just file anchors)."""
        ...

    def production_unwraps(
        self,
        limit: int = 50,
        *,
        include_tests: bool = False,
    ) -> list[dict[str, Any]]:
        """Return files with unwrap/expect in production code (excludes test files by default)."""
        ...
```

### Semantic Search (RAG) API

```python
from pathlib import Path
from doxslock.rsqt.rag import RsqtRag

# Create RAG instance with index path
rag = RsqtRag(store_path=Path(".rsqt.faiss"))

# Index RSQT.parquet (creates embeddings)
rag.index(Path("RSQT.parquet"))

# Or index only specific entity kinds
rag.index(Path("RSQT.parquet"), entity_kinds=["fn", "struct"])

# Or include allowlisted file anchors as chunks (hybrid)
rag.index(
    Path("RSQT.parquet"),
    chunk_strategy="hybrid",
    include_anchor_glob=["**/src/**/*.rs"],
)

# Later: load existing index
rag = RsqtRag(store_path=Path(".rsqt.faiss"))
rag.load(Path("RSQT.parquet"))

# Semantic search
results = rag.search("error handling", top_k=5)
for r in results:
    print(f"{r.chunk.title} (score: {r.score:.3f})")
    print(f"  Lines {r.chunk.line_start}-{r.chunk.line_end}")
    print(f"  {r.chunk.content[:100]}...")

# Get chunk count
print(f"Indexed {rag.chunk_count()} chunks")
```

### Chat API

```python
from pathlib import Path
from doxslock.rsqt.rag import RsqtRag
from doxslock.rag.chatbot import create_chatbot

# Load RAG index
rag = RsqtRag(store_path=Path(".rsqt.faiss"))
rag.load(Path("RSQT.parquet"))

# Create chatbot with backend
chatbot = create_chatbot(rag._retriever, backend="anthropic")
# Or: chatbot = create_chatbot(rag._retriever, backend="ollama", model="llama3.2:3b")

# Ask question
result = chatbot.ask("How does error handling work?", top_k=5)

print(f"Answer: {result['answer']}")
print(f"Model: {result['model']}")
print(f"Tokens: {result['tokens']}")
for src in result['sources']:
    print(f"  Source: {src['title']}")
```

### RsqtRag Class Reference

```python
class RsqtRag:
    def __init__(self, store_path: Path | None = None) -> None:
        """Initialize RsqtRag with optional index path."""
        ...

    def index(
        self,
        rsqt_parquet: Path,
        entity_kinds: list[str] | None = None,
        *,
        chunk_strategy: str = "hybrid",
        include_anchor_glob: list[str] | None = None,
        exclude_anchor_glob: list[str] | None = None,
        anchor_allowlist_mode: str = "extend",
        anchor_window_lines: int = 200,
        anchor_overlap_lines: int = 20,
        max_anchor_chars: int = 20000,
    ) -> None:
        """Index RSQT.parquet entities into FAISS.

        Args:
            rsqt_parquet: Path to RSQT.parquet file
            entity_kinds: Filter to specific kinds (fn, struct, etc.)
            chunk_strategy: "entities" (default), "files", or "hybrid"
            include_anchor_glob: Additional allowlist globs for file anchors
            exclude_anchor_glob: Additional denylist globs for file anchors
            anchor_allowlist_mode: "extend" (default+user) or "replace" (user-only)
            anchor_window_lines: Window size for anchor chunking (default: 200)
            anchor_overlap_lines: Overlap between windows (default: 20)
            max_anchor_chars: Truncate anchor source_text (default: 20000)
        """
        ...

    def load(self, rsqt_parquet: Path) -> None:
        """Load existing FAISS index from store_path (REQUIRED for staleness verification)."""
        ...

    def search(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        """Semantic search over indexed entities.

        Returns list of RetrievalResult with .chunk and .score attributes.
        """
        ...

    def chunk_count(self) -> int:
        """Return number of indexed chunks."""
        ...
```

## Schema Reference

RSQT indexes each `*.rs` file with the following columns:

### Identification (Track B v2.0+)

| Column | Type | Description |
|--------|------|-------------|
| `record_id` | String | Unique ID: `rsqt:{relative_path}` or `rsqt:{path}::{entity_id}` |
| `file_path` | String | Relative path from source root |
| `source_text` | String | Full file contents (file anchors) or entity source (entities) |
| `total_lines` | Int64 | Number of lines |
| `entity_kind` | String | **Track B**: `file` (anchor), `fn`, `trait`, `impl`, `struct`, `enum`, `macro`, `const`, `static`, `mod`, `type` |
| `entity_id` | String | **Track B**: Entity name (e.g., `process_data`, `Parser`) |
| `byte_start` | Int64 | **Track B**: Entity start byte offset (0-indexed) |
| `byte_end` | Int64 | **Track B**: Entity end byte offset (exclusive) |
| `line_start` | Int64 | **Track B**: Entity start line (1-indexed) |
| `line_end` | Int64 | **Track B**: Entity end line (1-indexed) |

### File Metadata

| Column | Type | Description |
|--------|------|-------------|
| `file_mtime` | String | Modification time (nanoseconds) |
| `file_size` | String | File size in bytes |
| `file_content_hash` | String | SHA-256 hash of content |
| `generated_at` | String | ISO timestamp of indexing |
| `rsqt_version` | String | RSQT version used |

### Module Info

| Column | Type | Description |
|--------|------|-------------|
| `has_tests` | Boolean | Has `#[test]` or `#[cfg(test)]` |
| `module_type` | String | `lib`, `bin`, `mod`, `test`, or `module` |
| `doc_comment` | String | First `//!` doc comment (module-level only) |
| `entity_doc_comment` | String | Per-entity docs from `///` and `#[doc=...]` (entity rows); empty for file anchors |

#### `doc_comment` vs `entity_doc_comment`

- `doc_comment` captures only the first inner doc comment (`//! ...`) for the file anchor row. It does **not** document individual items.
- `entity_doc_comment` captures outer item docs (`/// ...`) and doc attributes (`#[doc = \"...\"]`) that are directly attached to the extracted entity. Non-doc attributes (`#[...]`) between doc lines and the item are ignored for association.

### Public Surface

| Column | Type | Description |
|--------|------|-------------|
| `pub_fn_count` | Int64 | Count of `pub fn` |
| `pub_struct_count` | Int64 | Count of `pub struct` |
| `pub_trait_count` | Int64 | Count of `pub trait` |
| `impl_count` | Int64 | Count of `impl` blocks |

### Unsafe Surface

| Column | Type | Description |
|--------|------|-------------|
| `has_unsafe` | Boolean | Any unsafe code present |
| `unsafe_block_count` | Int64 | Count of `unsafe { }` blocks |
| `unsafe_fn_count` | Int64 | Count of `unsafe fn` |
| `unsafe_impl_count` | Int64 | Count of `unsafe impl` |
| `unsafe_trait_count` | Int64 | Count of `unsafe trait` |

### FFI / ABI Surface

| Column | Type | Description |
|--------|------|-------------|
| `has_ffi` | Boolean | Any FFI surface present |
| `extern_c_count` | Int64 | Count of `extern "C"` |
| `no_mangle_count` | Int64 | Count of `#[no_mangle]` |
| `repr_c_count` | Int64 | Count of `#[repr(C)]` |
| `static_mut_count` | Int64 | Count of `static mut` |
| `transmute_count` | Int64 | Count of `transmute` calls |
| `raw_ptr_count` | Int64 | Count of `*const`/`*mut` types |

### Runtime Failure Surface

| Column | Type | Description |
|--------|------|-------------|
| `has_panic` | Boolean | Has `panic!` macro |
| `panic_count` | Int64 | Count of `panic!` calls |
| `has_unwrap` | Boolean | Has `.unwrap()` calls |
| `unwrap_count` | Int64 | Count of `.unwrap()` calls |
| `expect_count` | Int64 | Count of `.expect()` calls |

### Test Boundary Detection (v3.1+)

| Column | Type | Description |
|--------|------|-------------|
| `**Clarification: prod vs test separation (v3.1)**  
RSQT v3.1 reports `prod_*` vs `test_*` metrics using **syntax-aware test-boundary detection** (tree-sitter / AST-guided),
not a simple “first marker in file” heuristic. When validating RSQT outputs, treat `rg`/regex counts as approximate baselines
and prefer file+line spot checks using RSQT’s returned locations.

test_code_lines` | Int64 | Lines inside `#[cfg(test)]` modules or `#[test]` functions |
| `prod_unwrap_count` | Int64 | Unwraps in production code only (excludes test code) |
| `prod_expect_count` | Int64 | Expects in production code only (excludes test code) |
| `prod_panic_count` | Int64 | Panics in production code only (excludes test code) |

**Note**: These columns use tree-sitter AST-based detection to accurately separate production code from test code. The detection covers:
- Files in `tests/` directory (entire file is test code)
- Inline `#[cfg(test)]` modules
- Individual `#[test]` functions

## Use Cases

### Security Audit

Find all files that need security review:

```bash
# Files with unsafe code
uv run rsqt unsafe

# Files with FFI (potential attack surface)
uv run rsqt ffi

# Files with transmute (memory reinterpretation) - CRITICAL
uv run rsqt transmute
```

### Code Quality Review

Find potential code quality issues:

```bash
# Files heavy on .unwrap() (error handling smell)
uv run rsqt query --columns file_path unwrap_count --limit 100 | sort -t: -k2 -rn | head -10

# Files with panic! (crash potential)
uv run rsqt query --columns file_path panic_count | grep -v ": 0"

# Large files (complexity smell)
uv run rsqt query --columns file_path total_lines --limit 100 | sort -t: -k2 -rn | head -10
```

### API Surface Analysis

Understand the public API:

```bash
# Files with public functions
uv run rsqt query --columns file_path pub_fn_count pub_struct_count pub_trait_count

# Find specific exports
uv run rsqt search "pub fn"
```

### Test Coverage Check

Find untested code:

```python
from doxslock.rsqt import RSQuery

q = RSQuery()

# Files WITHOUT tests
untested = q.query(has_tests=False, columns=["file_path", "total_lines"])
for row in untested:
    print(f"No tests: {row['file_path']} ({row['total_lines']} lines)")
```

### Codebase Understanding (RAG)

Use semantic search and chat to understand unfamiliar codebases:

```bash
# Build index (one-time)
uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss

# Explore architecture
uv run rsqt chat "What is the overall architecture of this codebase?" --index .rsqt.faiss --rsqt RSQT.parquet --backend ollama --model llama3.2:3b

# Find specific patterns
uv run rsqt rag-search "async error propagation" --index .rsqt.faiss --rsqt RSQT.parquet --top-k 10

# Understand specific features
uv run rsqt chat "How does the IPC system work between GUI and engine?" --index .rsqt.faiss --rsqt RSQT.parquet --backend anthropic

# Onboarding new team members
uv run rsqt chat "What are the main entry points for this application?" --index .rsqt.faiss --rsqt RSQT.parquet --backend openai
```

**Typical RAG Workflow:**

1. **Generate RSQT.parquet**: `uv run rsqt generate --full`
2. **Build FAISS index**: `uv run rsqt rag-index RSQT.parquet --output .rsqt.faiss`
3. **Search/Chat**: Use `rag-search` for quick lookups, `chat` for explanations

**Tips:**
- Use `rag-search` for finding relevant code (no LLM needed)
- Use `chat` for explanations and summaries (requires LLM)
- Ollama (`--backend ollama`) works offline with no API key
- Cloud backends (anthropic, openai, etc.) need API keys set as env vars

## How It Works

### Comment/String Masking

RSQT masks comments and string literals before pattern matching to avoid false positives:

```rust
// This comment mentions "unsafe" but isn't unsafe code
let msg = "unsafe is a keyword";  // Also not unsafe
unsafe { actual_unsafe_code(); }  // This IS detected
```

Only the actual `unsafe { }` block is counted, not the mentions in comments/strings.

### Skipped Directories

The following directories are automatically skipped:
- `target/` (Rust build output)
- `.git/`
- `node_modules/`
- Other common non-source directories

### Freshness Verification

RSQT tracks file content hashes. When you query:

1. If `fail_on_stale=False` (default), stale indexes are automatically regenerated
2. If `fail_on_stale=True`, a `StalenessError` is raised for stale indexes (useful for CI/CD)

## Track B Migration (v2.0+)

RSQT v2.0+ introduces **Track B** with multi-row entity extraction.

### What Changed

| Feature | Track A (v1.x) | Track B (v2.x) |
|---------|----------------|----------------|
| Rows per file | 1 (file anchor) | 1+ (file + entities) |
| Entity extraction | None | fn, trait, impl, macro, struct, enum, const, static, mod, type |
| Freshness binding | `*.rs` only | `*.rs`, `Cargo.toml`, `Cargo.lock` |
| Schema | 30 columns | 44 columns (+entity_kind, entity_id, byte_start, byte_end, line_start, line_end, test boundary columns, doc columns) |

### Backward Compatibility

**Default behavior is unchanged.** Existing code continues to work:

```python
# This still returns only .rs file anchors (not entities)
q = RSQuery()
rows = q.query(file="lib", columns=["file_path", "has_unsafe"])
stats = q.get_stats()  # Still counts only .rs files
```

**Why?** `RSQuery.query()` and `RSQuery.get_stats()` filter to `entity_kind == "file"` AND `file_path.endswith(".rs")` by default.

### New Entity Features

To query extracted entities (functions, traits, etc.):

**CLI:**
```bash
# Show entity distribution
uv run rsqt entities --stats

# List functions
uv run rsqt entities --kind fn --limit 20

# Find entities in specific file
uv run rsqt entities --file parser.rs

# JSON output
uv run rsqt entities --format json
```

**Python:**
```python
from doxslock.rsqt import RSQuery

q = RSQuery()

# Get entity distribution
stats = q.entity_stats()
# {'fn': 47, 'impl': 12, 'struct': 8, 'trait': 3, ...}

# Query specific entities
fns = q.entities(kind="fn", file="parser", limit=20)
for e in fns:
    print(f"{e['file_path']}:{e['line_start']}-{e['line_end']} {e['entity_id']}")
```

### Including All Proof Files

Track B indexes proof files (`Cargo.toml`, `Cargo.lock`) alongside `.rs` files for freshness verification.

By default, these are excluded from query results. To include them:

```bash
# Include Cargo.toml in stats
uv run rsqt stats --include-all-files

# Include Cargo.toml in query
uv run rsqt query --columns file_path --include-all-files
```

```python
# Include all proof files
stats = q.get_stats(include_all_files=True)
rows = q.query(include_all_files=True)
```

### Freshness Verification

Track B binds freshness to multiple glob patterns via `proof_globs` metadata:

- `**/*.rs` - Rust source files
- `**/Cargo.toml` - Package manifest (dependencies)
- `**/Cargo.lock` - Locked versions

If **any** proof file changes, the index becomes stale. This catches:
- Dependency changes (new crate in `Cargo.toml`)
- Locked version changes (`Cargo.lock` regenerated)
- Source code changes (`*.rs`)

### Consumer Migration Checklist

If you consume RSQT.parquet directly (not via RSQuery):

1. **Filter file anchors**: `WHERE entity_kind = 'file'`
2. **Filter .rs files**: `WHERE file_path LIKE '%.rs'` (unless you want Cargo.toml)
3. **Handle new columns**: `entity_kind`, `entity_id`, `line_start`, `line_end` are now present

Example Polars:
```python
import polars as pl

df = pl.read_parquet("RSQT.parquet")

# Get only .rs file anchors (backward compatible)
files = df.filter(
    (pl.col("entity_kind") == "file") &
    (pl.col("file_path").str.ends_with(".rs"))
)

# Get entities only
entities = df.filter(pl.col("entity_kind") != "file")
```

## Troubleshooting

### "No RSQT.parquet found"

Run `uv run rsqt generate --full` to create the index.

### "StalenessError: RSQT parquet is stale"

The index is out of date. Regenerate:

```bash
uv run rsqt generate --full
```

Or use `fail_on_stale=False` in Python (the default):

```python
q = RSQuery(fail_on_stale=False)  # Default - auto-refresh stale indexes
```

### Missing files in results

Check if files are in skipped directories (`target/`, etc.) or don't have `.rs` extension.

### Counts seem wrong

Pattern matching is heuristic-based (not AST-based). Edge cases:
- Macros that generate unsafe code aren't detected
- Complex attribute syntax might not match
- The `extern "C"` pattern requires the exact string format

---

**Version**: 3.1.0
**Last Updated**: 2026-02-07
**Changes**:
- v3.1.0: Added `audit` command (unified FINDINGS.jsonl from safety/supply-chain/doc rules), `doc-findings` command (documentation-derived findings), and test boundary detection columns (`test_code_lines`, `prod_unwrap_count`, `prod_expect_count`, `prod_panic_count`) using tree-sitter AST for deterministic prod vs test code separation
- v3.0.0: Added composite analysis commands: `health` (codebase health grade A+ to F), `risk-hotspots` (multi-factor risk analysis), `coverage-risk` (untested public API exposure)
- v2.9.0: Added `impls` command for impl block complexity analysis (`impl_count`)
- v2.8.0: Added `test-coverage` command for test coverage audit (`has_tests`), `modules` command for module type distribution (`module_type`), and `api-surface` command for public API surface (`pub_fn_count`, `pub_struct_count`, `pub_trait_count`)
- v2.7.0: Added `panics` command for reliability audit of `panic!` macro usage
- v2.6.0: Added `raw-ptrs` command for raw pointer (`*const`/`*mut`) safety review
- v2.5.0: Added `transmute` command for CRITICAL safety audit of `mem::transmute` usage
- v2.4.0: Added `docs` command for extracting documentation structure (module `//!` and entity `///` docs) with hierarchical JSON output
- v2.3.0: Added `dump` command for JSON export; added `columns` command for schema discovery; added `available_columns()` and `sum_column()` to Python API
- v2.2.0: Added `--include-entities` flag to `rsqt query` for unified entity access; fixed entity name parsing for types with `::`
- v2.1.0: RAG support - `rag-index`, `rag-search`, `chat` commands for semantic search and LLM Q&A
- v2.0.0: Track B migration - multi-row entity extraction, proof_globs freshness, `rsqt entities` command
