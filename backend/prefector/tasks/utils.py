from datetime import datetime, timezone
from typing import Any, Dict

from models.enums import InstrumentType
from models.instrument import Instrument


def convert_xtinstrument_to_instrument(instrument_detail: Dict[str, Any]) -> Instrument:
  """
  Convert an XTInstrument object to an Instrument object.

  Args:
      xt_instrument (XTInstrument): The XTInstrument object to convert.

  Returns:
      Instrument: The converted Instrument object.
  """
  # 创建 Instrument 对象
  instrument = Instrument(
    # 基础标识信息
    id=f"{instrument_detail.get('InstrumentID', '')}.{instrument_detail.get('ExchangeID', '')}",
    market=instrument_detail.get("ExchangeID"),  # ExchangeID -> market
    instrument_id=instrument_detail.get("InstrumentID"),  # InstrumentID -> code
    name=instrument_detail.get("InstrumentName"),  # InstrumentName -> name
    type=InstrumentType(instrument_detail.get("InstrumentType"))
    if instrument_detail.get("InstrumentType")
    else None,  # InstrumentType -> type
    abbreviation=instrument_detail.get("Abbreviation"),  # Abbreviation -> abbreviation
    product_id=instrument_detail.get("ProductID"),  # ProductID -> product_id
    product_name=instrument_detail.get("ProductName"),  # ProductName -> product_name
    underlying_code=instrument_detail.get(
      "UnderlyingCode"
    ),  # UnderlyingCode -> underlying_code
    extend_name=instrument_detail.get("ExtendName"),  # ExtendName -> extend_name
    exchange_code=instrument_detail.get(
      "ExchangeCode"
    ),  # ExchangeCode -> exchange_code
    rzrk_code=instrument_detail.get("RzrkCode"),  # RzrkCode -> rzrk_code
    uni_code=instrument_detail.get("UniCode"),  # UniCode -> uni_code
    # 重要日期
    create_date=datetime.strptime(instrument_detail.get("CreateDate", "0"), "%Y%m%d")
    if instrument_detail.get("CreateDate", "0") != "0"
    else None,  # CreateDate -> create_date
    open_date=datetime.strptime(instrument_detail.get("OpenDate", "0"), "%Y%m%d")
    if instrument_detail.get("OpenDate", "0") != "0"
    else None,  # OpenDate -> open_date
    expire_date=datetime.fromtimestamp(
      int(instrument_detail.get("ExpireDate", "0")), timezone.utc
    )
    if instrument_detail.get("ExpireDate", "0") != "0"
    else None,  # ExpireDate -> expire_date
    # 价格信息
    pre_close=instrument_detail.get("PreClose"),  # PreClose -> pre_close
    settlement_price=instrument_detail.get(
      "SettlementPrice"
    ),  # SettlementPrice -> settlement_price
    up_stop_price=instrument_detail.get("UpStopPrice"),  # UpStopPrice -> up_stop_price
    down_stop_price=instrument_detail.get(
      "DownStopPrice"
    ),  # DownStopPrice -> down_stop_price
    # 股本信息
    float_volume=instrument_detail.get("FloatVolume"),  # FloatVolume -> float_volume
    total_volume=instrument_detail.get("TotalVolume"),  # TotalVolume -> total_volume
    accumulated_interest=instrument_detail.get(
      "AccumulatedInterest"
    ),  # AccumulatedInterest -> accumulated_interest
    # 保证金和交易参数
    long_margin_ratio=instrument_detail.get(
      "LongMarginRatio"
    ),  # LongMarginRatio -> long_margin_ratio
    short_margin_ratio=instrument_detail.get(
      "ShortMarginRatio"
    ),  # ShortMarginRatio -> short_margin_ratio
    price_tick=instrument_detail.get("PriceTick"),  # PriceTick -> price_tick
    volume_multiple=instrument_detail.get(
      "VolumeMultiple"
    ),  # VolumeMultiple -> volume_multiple
    main_contract=instrument_detail.get(
      "MainContract"
    ),  # MainContract -> main_contract
    # 订单限制
    max_market_order_volume=instrument_detail.get(
      "MaxMarketOrderVolume"
    ),  # MaxMarketOrderVolume -> max_market_order_volume
    min_market_order_volume=instrument_detail.get(
      "MinMarketOrderVolume"
    ),  # MinMarketOrderVolume -> min_market_order_volume
    max_limit_order_volume=instrument_detail.get(
      "MaxLimitOrderVolume"
    ),  # MaxLimitOrderVolume -> max_limit_order_volume
    min_limit_order_volume=instrument_detail.get(
      "MinLimitOrderVolume"
    ),  # MinLimitOrderVolume -> min_limit_order_volume
    max_margin_side_algorithm=instrument_detail.get(
      "MaxMarginSideAlgorithm"
    ),  # MaxMarginSideAlgorithm -> max_margin_side_algorithm
    # 交易状态和特征
    day_count_from_ipo=instrument_detail.get(
      "DayCountFromIPO"
    ),  # DayCountFromIPO -> day_count_from_ipo
    last_volume=instrument_detail.get("LastVolume"),  # LastVolume -> last_volume
    instrument_status=instrument_detail.get(
      "InstrumentStatus"
    ),  # InstrumentStatus -> instrument_status
    is_trading=instrument_detail.get("IsTrading"),  # IsTrading -> is_trading
    is_recent=instrument_detail.get("IsRecent"),  # IsRecent -> is_recent
    is_continuous=instrument_detail.get(
      "IsContinuous"
    ),  # IsContinuous -> is_continuous
    b_not_profitable=instrument_detail.get(
      "bNotProfitable"
    ),  # bNotProfitable -> b_not_profitable
    b_dual_class=instrument_detail.get("bDualClass"),  # bDualClass -> b_dual_class
    continue_type=instrument_detail.get(
      "ContinueType"
    ),  # ContinueType -> continue_type
    secu_category=instrument_detail.get(
      "secuCategory"
    ),  # secuCategory -> secu_category
    secu_attri=instrument_detail.get("secuAttri"),  # secuAttri -> secu_attri
    # 额外订单限制
    max_market_sell_order_volume=instrument_detail.get(
      "MaxMarketSellOrderVolume"
    ),  # MaxMarketSellOrderVolume -> max_market_sell_order_volume
    min_market_sell_order_volume=instrument_detail.get(
      "MinMarketSellOrderVolume"
    ),  # MinMarketSellOrderVolume -> min_market_sell_order_volume
    max_limit_sell_order_volume=instrument_detail.get(
      "MaxLimitSellOrderVolume"
    ),  # MaxLimitSellOrderVolume -> max_limit_sell_order_volume
    min_limit_sell_order_volume=instrument_detail.get(
      "MinLimitSellOrderVolume"
    ),  # MinLimitSellOrderVolume -> min_limit_sell_order_volume
    max_fixed_buy_order_vol=instrument_detail.get(
      "MaxFixedBuyOrderVol"
    ),  # MaxFixedBuyOrderVol -> max_fixed_buy_order_vol
    min_fixed_buy_order_vol=instrument_detail.get(
      "MinFixedBuyOrderVol"
    ),  # MinFixedBuyOrderVol -> min_fixed_buy_order_vol
    max_fixed_sell_order_vol=instrument_detail.get(
      "MaxFixedSellOrderVol"
    ),  # MaxFixedSellOrderVol -> max_fixed_sell_order_vol
    min_fixed_sell_order_vol=instrument_detail.get(
      "MinFixedSellOrderVol"
    ),  # MinFixedSellOrderVol -> min_fixed_sell_order_vol
    # 特殊属性
    hsgt_flag=instrument_detail.get("HSGTFlag"),  # HSGTFlag -> hsgt_flag
    bond_par_value=instrument_detail.get(
      "BondParValue"
    ),  # BondParValue -> bond_par_value
    qualified_type=instrument_detail.get(
      "QualifiedType"
    ),  # QualifiedType -> qualified_type
    price_tick_type=instrument_detail.get(
      "PriceTickType"
    ),  # PriceTickType -> price_tick_type
    trading_status=instrument_detail.get(
      "tradingStatus"
    ),  # tradingStatus -> trading_status
    # 期权相关
    opt_unit=instrument_detail.get("OptUnit"),  # OptUnit -> opt_unit
    margin_unit=instrument_detail.get("MarginUnit"),  # MarginUnit -> margin_unit
    opt_undl_code=instrument_detail.get("OptUndlCode"),  # OptUndlCode -> opt_undl_code
    opt_undl_market=instrument_detail.get(
      "OptUndlMarket"
    ),  # OptUndlMarket -> opt_undl_market
    opt_lot_size=instrument_detail.get("OptLotSize"),  # OptLotSize -> opt_lot_size
    opt_exercise_price=instrument_detail.get(
      "OptExercisePrice"
    ),  # OptExercisePrice -> opt_exercise_price
    neeq_exe_type=instrument_detail.get("NeeqExeType"),  # NeeqExeType -> neeq_exe_type
    opt_exch_fixed_margin=instrument_detail.get(
      "OptExchFixedMargin"
    ),  # OptExchFixedMargin -> opt_exch_fixed_margin
    opt_exch_mini_margin=instrument_detail.get(
      "OptExchMiniMargin"
    ),  # OptExchMiniMargin -> opt_exch_mini_margin
    # 货币和附加信息
    ccy=instrument_detail.get("Ccy"),  # Ccy -> ccy
    ib_sec_type=instrument_detail.get("IbSecType"),  # IbSecType -> ib_sec_type
    opt_undl_risk_free_rate=instrument_detail.get(
      "OptUndlRiskFreeRate"
    ),  # OptUndlRiskFreeRate -> opt_undl_risk_free_rate
    opt_undl_history_rate=instrument_detail.get(
      "OptUndlHistoryRate"
    ),  # OptUndlHistoryRate -> opt_undl_history_rate
    end_deliv_date=instrument_detail.get(
      "EndDelivDate"
    ),  # EndDelivDate -> end_deliv_date
    registered_capital=instrument_detail.get(
      "RegisteredCapital"
    ),  # RegisteredCapital -> registered_capital
    max_order_price_range=instrument_detail.get(
      "MaxOrderPriceRange"
    ),  # MaxOrderPriceRange -> max_order_price_range
    min_order_price_range=instrument_detail.get(
      "MinOrderPriceRange"
    ),  # MinOrderPriceRange -> min_order_price_range
    vote_right_ratio=instrument_detail.get(
      "VoteRightRatio"
    ),  # VoteRightRatio -> vote_right_ratio
    # 回购限制
    m_n_min_repurchase_days_limit=instrument_detail.get(
      "m_nMinRepurchaseDaysLimit"
    ),  # m_nMinRepurchaseDaysLimit -> m_n_min_repurchase_days_limit
    m_n_max_repurchase_days_limit=instrument_detail.get(
      "m_nMaxRepurchaseDaysLimit"
    ),  # m_nMaxRepurchaseDaysLimit -> m_n_max_repurchase_days_limit
    interest_accrual_days=instrument_detail.get(
      "InterestAccrualDays"
    ),  # InterestAccrualDays -> interest_accrual_days
    # 交割信息
    delivery_year=instrument_detail.get(
      "DeliveryYear"
    ),  # DeliveryYear -> delivery_year
    delivery_month=instrument_detail.get(
      "DeliveryMonth"
    ),  # DeliveryMonth -> delivery_month
    contract_type=instrument_detail.get(
      "ContractType"
    ),  # ContractType -> contract_type
    # 交易配额
    product_trade_quota=instrument_detail.get(
      "ProductTradeQuota"
    ),  # ProductTradeQuota -> product_trade_quota
    contract_trade_quota=instrument_detail.get(
      "ContractTradeQuota"
    ),  # ContractTradeQuota -> contract_trade_quota
    product_open_interest_quota=instrument_detail.get(
      "ProductOpenInterestQuota"
    ),  # ProductOpenInterestQuota -> product_open_interest_quota
    contract_open_interest_quota=instrument_detail.get(
      "ContractOpenInterestQuota"
    ),  # ContractOpenInterestQuota -> contract_open_interest_quota
    # 费用和手续费信息
    charge_type=instrument_detail.get("ChargeType"),  # ChargeType -> charge_type
    charge_open=instrument_detail.get("ChargeOpen"),  # ChargeOpen -> charge_open
    charge_close=instrument_detail.get("ChargeClose"),  # ChargeClose -> charge_close
    charge_today_open=instrument_detail.get(
      "ChargeTodayOpen"
    ),  # ChargeTodayOpen -> charge_today_open
    charge_today_close=instrument_detail.get(
      "ChargeTodayClose"
    ),  # ChargeTodayClose -> charge_today_close
    # 期权特定
    option_type=instrument_detail.get("OptionType"),  # OptionType -> option_type
    open_interest_multiple=instrument_detail.get(
      "OpenInterestMultiple"
    ),  # OpenInterestMultiple -> open_interest_multiple
  )
  return instrument
