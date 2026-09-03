"""Command-line entry point for the gated P1 execution-owner reconciliation."""

from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_import_paths() -> None:
  repository_root = Path(__file__).resolve().parents[1]
  source_roots = (
    repository_root / "packages" / "infrastructure" / "src",
    repository_root / "packages" / "contracts" / "src",
    repository_root / "packages" / "domain" / "src",
  )
  for source_root in reversed(source_roots):
    source_text = str(source_root)
    if source_text not in sys.path:
      sys.path.insert(0, source_text)


_bootstrap_import_paths()


def main(argv: list[str] | None = None) -> int:
  """Delegate after bootstrapping source-checkout imports."""

  from quantx_infrastructure.services.t_assistant_p1_reconcile import (
    main as reconcile_main,
  )

  return reconcile_main(argv)


if __name__ == "__main__":
  raise SystemExit(main())
