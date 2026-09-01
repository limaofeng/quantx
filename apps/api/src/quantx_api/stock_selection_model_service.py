"""Manual registration boundary for finalized selection model evidence."""

from __future__ import annotations

from datetime import date

from quantx_infrastructure.repositories.stock_selection_repository import (
  StockSelectionRepository,
)
from quantx_infrastructure.services.stock_selection_artifacts import (
  SelectionArtifactError,
  load_selection_artifact,
)

from quantx_api.research_artifacts import ResearchArtifactStore


class StockSelectionModelService:
  def __init__(self, repository: StockSelectionRepository):
    self.repository = repository

  async def register(self, run_key: str):
    summary = ResearchArtifactStore().get_summary(run_key)
    if summary is None:
      raise ValueError("研究运行不存在")
    if (
      summary.study_id != "next-day-selection"
      or summary.version != "v1"
      or summary.status != "success"
    ):
      raise ValueError("只能登记成功的 next-day-selection v1 运行")
    try:
      bundle = load_selection_artifact(summary.run_directory)
    except SelectionArtifactError as exc:
      raise ValueError(f"模型产物未通过安全校验: {exc}") from exc
    manifest = bundle.manifest
    metrics = bundle.metrics
    gates = metrics.get("gates")
    if not isinstance(gates, dict):
      raise ValueError("模型评估缺少发布门禁")
    return await self.repository.register_model(
      {
        "model_version": manifest["model_version"],
        "run_key": run_key,
        "artifact_directory": str(bundle.directory),
        "artifact_manifest_sha256": bundle.manifest_sha256,
        "selected_family": manifest["selected_family"],
        "indicator_version": manifest["indicator_version"],
        "factor_set_version": manifest["factor_set_version"],
        "factor_set_hash": manifest["factor_set_hash"],
        "label_version": manifest["label_version"],
        "calibrator_version": manifest["calibrator_version"],
        "training_start": date.fromisoformat(manifest["training_start"]),
        "training_end": date.fromisoformat(manifest["training_end"]),
        "calibration_start": date.fromisoformat(manifest["calibration_start"]),
        "calibration_end": date.fromisoformat(manifest["calibration_end"]),
        "test_start": date.fromisoformat(manifest["test_start"]),
        "test_end": date.fromisoformat(manifest["test_end"]),
        "historical_universe_complete": bool(gates.get("historical_universe_complete")),
        "effect_gate_passed": bool(gates.get("effect_gate_passed")),
        "metrics": metrics,
        "gates": gates,
        "evidence": {
          "config_hash": manifest.get("config_hash"),
          "data_fingerprint": manifest.get("data_fingerprint"),
          "artifact_count": len(manifest.get("artifacts") or []),
          "data_quality": bundle.data_quality,
        },
        "approved_by": "",
      }
    )
