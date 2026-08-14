from datetime import date, datetime

import pytest
from quantx_infrastructure.repositories.financial_metric_snapshot_repository import (
  FinancialMetricSnapshotRepository,
)
from sqlalchemy.dialects import postgresql


class _Session:
  def __init__(self) -> None:
    self.statements = []

  async def execute(self, statement):
    self.statements.append(statement)


@pytest.mark.asyncio
async def test_metric_upsert_persists_independent_roe_quality_state() -> None:
  session = _Session()
  repo = FinancialMetricSnapshotRepository(session)

  saved = await repo.bulk_upsert(
    [
      {
        "code": "000001.SZ",
        "as_of_date": date(2026, 4, 20),
        "report_date": date(2025, 12, 31),
        "announce_date": date(2026, 4, 20),
        "roe_ttm": 12.3,
        "roe_quality_status": "VALID",
        "roe_quality_flags": [],
        "quality_status": "partial",
        "quality_flags": ["missing_previous_period_revenue"],
        "calculated_at": datetime(2026, 4, 20, 16, 0),
      }
    ]
  )

  assert saved == 1
  assert len(session.statements) == 2
  metric_sql, quality_sql = [
    str(statement.compile(dialect=postgresql.dialect()))
    for statement in session.statements
  ]
  assert "INSERT INTO financial_metric_snapshots" in metric_sql
  assert "roe_quality_status" not in metric_sql
  assert "INSERT INTO financial_metric_roe_qualities" in quality_sql
  assert "status" in quality_sql
  assert "ON CONFLICT" in quality_sql
