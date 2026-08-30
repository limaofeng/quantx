import pytest
from quantx_infrastructure.config.settings import settings
from sqlalchemy.engine import make_url


def _database_guard(request: pytest.FixtureRequest):
  for _name, plugin in request.config.pluginmanager.list_name_plugin():
    guard = getattr(plugin, "_isolated_test_database_url", None)
    if callable(guard):
      return guard
  raise AssertionError("root pytest database guard is not loaded")


def test_pytest_uses_a_dedicated_database() -> None:
  database_name = str(make_url(settings.database_url).database or "")

  assert settings.environment == "testing"
  assert database_name != "quantx"
  assert database_name.endswith("_test") or database_name.startswith("test_")


def test_dev_database_url_is_rewritten_to_dedicated_test_database(
  monkeypatch: pytest.MonkeyPatch,
  request: pytest.FixtureRequest,
) -> None:
  monkeypatch.delenv("QUANTX_TEST_DATABASE_URL", raising=False)
  monkeypatch.delenv("QUANTX_TEST_DATABASE_NAME", raising=False)
  monkeypatch.setenv(
    "DATABASE_URL",
    "postgresql+asyncpg://tester:secret@database.example:5432/quantx",
  )

  isolated = make_url(_database_guard(request)())

  assert isolated.database == "quantx_test"
  assert isolated.host == "database.example"


def test_explicit_test_database_cannot_target_dev_quantx(
  monkeypatch: pytest.MonkeyPatch,
  request: pytest.FixtureRequest,
) -> None:
  monkeypatch.setenv(
    "QUANTX_TEST_DATABASE_URL",
    "postgresql+asyncpg://tester:secret@database.example:5432/quantx",
  )
  monkeypatch.delenv("QUANTX_TEST_DATABASE_NAME", raising=False)

  with pytest.raises(RuntimeError, match="dedicated to tests"):
    _database_guard(request)()
