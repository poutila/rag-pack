from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class PluginOutputs:
    """Additional outputs produced by a plugin, to be merged into RUN_MANIFEST.json."""
    files: Dict[str, str]              # logical_name -> relative filename (within out_dir)
    metrics: Dict[str, Any]            # arbitrary JSON-serializable metrics
    hashes: Dict[str, str | None]      # logical_name -> sha256 hex (or None)


@dataclass(frozen=True)
class PluginContext:
    pack_path: Path
    out_dir: Path
    pack: Any                          # run_pack.Pack (kept loose to avoid import cycles)
    args: Any                          # argparse.Namespace
    engine: str
    pack_type: str


class PackPlugin:
    """Hook points for pack-specific behavior.

    The runner stays generic; plugins add domain-specific post-processing (reports, findings, extra validators).
    """
    name: str = "base"

    def applies(self, *, engine: str, pack_type: str) -> bool:
        return False

    def post_run(self, ctx: PluginContext) -> Optional[PluginOutputs]:
        return None
