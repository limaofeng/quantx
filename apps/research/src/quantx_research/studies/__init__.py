"""Built-in offline research studies."""

from quantx_research.studies.first_board_promotion import (
  FirstBoardPromotionStudy,
  FirstBoardResearchConfig,
  FirstBoardResearchResult,
)
from quantx_research.studies.volume_shock import VolumeShockStudy

__all__ = [
  "FirstBoardPromotionStudy",
  "FirstBoardResearchConfig",
  "FirstBoardResearchResult",
  "VolumeShockStudy",
]
