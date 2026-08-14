from quantx_api.gqlapi.resolvers.portfolio_overview import (
  resolve_position_instrument_name,
)


def test_position_name_uses_catalog_when_snapshot_repeats_full_code():
  assert (
    resolve_position_instrument_name(
      stock_code="688552.SH",
      position_name="688552.SH",
      catalog_name="航天南湖",
    )
    == "航天南湖"
  )


def test_position_name_uses_catalog_when_snapshot_repeats_short_code():
  assert (
    resolve_position_instrument_name(
      stock_code="302132.SZ",
      position_name="302132",
      catalog_name="中航成飞",
    )
    == "中航成飞"
  )


def test_position_name_preserves_a_real_snapshot_name():
  assert (
    resolve_position_instrument_name(
      stock_code="600000.SH",
      position_name="浦发银行",
      catalog_name="上海浦东发展银行股份有限公司",
    )
    == "浦发银行"
  )


def test_position_name_falls_back_to_normalized_code():
  assert (
    resolve_position_instrument_name(
      stock_code=" 688552.sh ",
      position_name=None,
      catalog_name=None,
    )
    == "688552.SH"
  )
