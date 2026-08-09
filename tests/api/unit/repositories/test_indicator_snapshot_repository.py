import pytest
from quantx_infrastructure.repositories.indicator_snapshot_repository import (
  _is_st_stock_name,
)


@pytest.mark.parametrize(
  ("name", "expected"),
  [
    ("ST华信", True),
    ("*ST泛海", True),
    ("S*ST佳通", True),
    ("SST天海", True),
    ("  st中安  ", True),
    ("华信科技", False),
    ("华ST科技", False),
    ("ETF证券", False),
    (None, False),
  ],
)
def test_is_st_stock_name_matches_a_share_risk_prefixes(name, expected):
  assert _is_st_stock_name(name) is expected
