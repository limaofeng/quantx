import pytest
import sqlalchemy
from quantx_infrastructure.database.relational import (
  _prepare_runtime_message_boxes,
)
from sqlalchemy import create_engine, inspect, text


class FakeConnection:
  def __init__(self) -> None:
    self.statements: list[str] = []

  def execute(self, statement) -> None:
    self.statements.append(str(statement))


class FakeInspector:
  def get_table_names(self) -> list[str]:
    return ["agent_report_inbox"]

  def get_columns(self, table_name: str) -> list[dict]:
    assert table_name == "agent_report_inbox"
    return [
      {"name": "message_id", "nullable": False},
      {"name": "business_idempotency_key", "nullable": False},
    ]

  def get_indexes(self, table_name: str) -> list[dict]:
    assert table_name == "agent_report_inbox"
    return []

  def get_unique_constraints(self, table_name: str) -> list[dict]:
    assert table_name == "agent_report_inbox"
    return [
      {
        "name": "uq_agent_report_business_idempotency",
        "unique": True,
      }
    ]


def test_existing_report_inbox_receives_retry_columns(monkeypatch) -> None:
  inspector = FakeInspector()
  monkeypatch.setattr(sqlalchemy, "inspect", lambda connection: inspector)
  connection = FakeConnection()

  _prepare_runtime_message_boxes(connection)

  statements = "\n".join(connection.statements)
  assert (
    "ADD COLUMN processing_attempts INTEGER NOT NULL DEFAULT 0"
    in statements
  )
  assert "ADD COLUMN next_attempt_at TIMESTAMP" in statements


def test_draft_market_data_tables_are_renamed_without_losing_rows() -> None:
  engine = create_engine("sqlite:///:memory:")
  with engine.begin() as connection:
    connection.execute(
      text(
        "CREATE TABLE market_data_requests "
        "(request_id VARCHAR(64) PRIMARY KEY, payload TEXT)"
      )
    )
    connection.execute(
      text(
        "CREATE TABLE market_data_transfers "
        "(transfer_id VARCHAR(64) PRIMARY KEY, payload TEXT)"
      )
    )
    connection.execute(
      text(
        "INSERT INTO market_data_requests (request_id, payload) "
        "VALUES ('request-1', 'request-payload')"
      )
    )
    connection.execute(
      text(
        "INSERT INTO market_data_transfers (transfer_id, payload) "
        "VALUES ('transfer-1', 'transfer-payload')"
      )
    )

    _prepare_runtime_message_boxes(connection)

    tables = set(inspect(connection).get_table_names())
    assert "market_data_requests" not in tables
    assert "market_data_transfers" not in tables
    assert "market_data_request" in tables
    assert "market_data_transfer" in tables
    assert connection.scalar(
      text(
        "SELECT payload FROM market_data_request "
        "WHERE request_id = 'request-1'"
      )
    ) == "request-payload"
    assert connection.scalar(
      text(
        "SELECT payload FROM market_data_transfer "
        "WHERE transfer_id = 'transfer-1'"
      )
    ) == "transfer-payload"

  engine.dispose()


def test_ambiguous_draft_and_final_tables_fail_closed() -> None:
  engine = create_engine("sqlite:///:memory:")
  with engine.begin() as connection:
    connection.execute(
      text("CREATE TABLE market_data_requests (request_id VARCHAR(64))")
    )
    connection.execute(
      text("CREATE TABLE market_data_request (request_id VARCHAR(64))")
    )

    with pytest.raises(RuntimeError, match="ambiguous merge"):
      _prepare_runtime_message_boxes(connection)

  engine.dispose()
