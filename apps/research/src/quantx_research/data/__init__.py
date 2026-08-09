"""QuantX 离线研究的只读数据访问与数据集构建。"""

from .adjustments import apply_dividend_adjustment
from .dataset_builder import DatasetBuilder
from .factor_coverage import (
  DIVIDEND_FACTOR_SOURCE,
  DividendFactorCoverageError,
  build_dividend_factor_coverage_report,
)
from .models import (
  CANONICAL_BAR_COLUMNS,
  FACTOR_COLUMNS,
  INSTRUMENT_COLUMNS,
  AdjustmentMode,
  DataQualityReport,
  DividendFactorCoverageReport,
  ResearchDataset,
  SymbolCoverage,
)
from .normalization import (
  normalize_daily_bars,
  normalize_dividend_factors,
  normalize_instruments,
)
from .parquet_cache import ParquetDatasetCache
from .qmt_archive_source import (
  FULL_A_SHARE_REQUIRED_REQUEST_COUNT,
  QMT_DAILY_BAR_ARCHIVE_FORMAT,
  QMT_DAILY_BAR_ARCHIVE_SCHEMA_VERSION,
  QmtDailyBarArchiveError,
  QmtDailyBarArchiveResearchDataSource,
  describe_qmt_daily_bar_archive,
)
from .quality import build_quality_report, combine_quality_reports
from .source import InfrastructureResearchDataSource, ResearchDataSource

__all__ = [
  "CANONICAL_BAR_COLUMNS",
  "FACTOR_COLUMNS",
  "FULL_A_SHARE_REQUIRED_REQUEST_COUNT",
  "INSTRUMENT_COLUMNS",
  "AdjustmentMode",
  "DataQualityReport",
  "DatasetBuilder",
  "DIVIDEND_FACTOR_SOURCE",
  "DividendFactorCoverageError",
  "DividendFactorCoverageReport",
  "InfrastructureResearchDataSource",
  "ParquetDatasetCache",
  "QMT_DAILY_BAR_ARCHIVE_FORMAT",
  "QMT_DAILY_BAR_ARCHIVE_SCHEMA_VERSION",
  "QmtDailyBarArchiveError",
  "QmtDailyBarArchiveResearchDataSource",
  "ResearchDataSource",
  "ResearchDataset",
  "SymbolCoverage",
  "apply_dividend_adjustment",
  "build_dividend_factor_coverage_report",
  "build_quality_report",
  "combine_quality_reports",
  "describe_qmt_daily_bar_archive",
  "normalize_daily_bars",
  "normalize_dividend_factors",
  "normalize_instruments",
]
