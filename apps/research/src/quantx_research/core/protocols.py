"""Extension protocol for offline research studies."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import pandas as pd

from quantx_research.core.config import StudyConfig
from quantx_research.core.models import StudyResult


@runtime_checkable
class ResearchStudy(Protocol):
  study_id: str
  version: str
  required_columns: tuple[str, ...]

  @property
  def required_lookback(self) -> int: ...

  def build_events(
    self,
    panel: pd.DataFrame,
    config: StudyConfig,
    benchmark: pd.DataFrame | None = None,
  ) -> pd.DataFrame: ...

  def analyze(
    self,
    events: pd.DataFrame,
    config: StudyConfig,
  ) -> StudyResult: ...
