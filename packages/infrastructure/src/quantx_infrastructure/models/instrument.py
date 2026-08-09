"""
数据库模型 - 金融产品相关数据模型
"""

from sqlalchemy import Boolean, Column, Date, Enum, Float, Integer, String

from quantx_infrastructure.database.relational_base import Base, TimestampMixin
from quantx_infrastructure.models.enums import InstrumentType


class Instrument(Base, TimestampMixin):
  """金融产品信息表"""

  __tablename__ = "instruments"

  # Keep original id field unchanged
  id = Column("code", String(10), primary_key=True, index=True, comment="主键")

  # Exchange and basic info
  # 合约市场代码 - ExchangeID
  market = Column("exchange_id", String(20), comment="合约市场代码")
  # 合约代码 - InstrumentID
  instrument_id = Column(
    "instrument_id", String(50), nullable=False, comment="合约代码"
  )
  # 合约名称 - InstrumentName
  name = Column("instrument_name", String(200), comment="合约名称")
  # 合约类型 e.g., 股票、期货、期权、债券等 - InstrumentType
  type = Column(
    "instrument_type", Enum(InstrumentType, name="instrument_type"), comment="合约类型"
  )
  # 合约名称的拼音简写 - Abbreviation
  abbreviation = Column(String(100), comment="合约名称的拼音简写")
  # 合约的品种ID（期货） - ProductID
  product_id = Column(String(50), comment="合约的品种ID（期货）")
  # 合约的品种名称（期货） - ProductName
  product_name = Column(String(100), comment="合约的品种名称（期货）")
  # 标的合约 - UnderlyingCode
  underlying_code = Column(String(50), comment="标的合约")
  # 扩位名称 - ExtendName
  extend_name = Column(String(200), comment="扩位名称")
  # 交易所代码 - ExchangeCode
  exchange_code = Column(String(20), comment="交易所代码")
  # rzrk代码 - RzrkCode
  rzrk_code = Column(String(50), comment="rzrk代码")
  # 统一规则代码 - UniCode
  uni_code = Column(String(50), comment="统一规则代码")

  # Important dates
  # 上市日期（期货） - CreateDate
  create_date = Column(Date, comment="上市日期（期货）")
  # IPO日期（股票） - OpenDate
  open_date = Column(Date, comment="IPO日期（股票）")
  # 退市日或者到期日 - ExpireDate
  expire_date = Column(Date, comment="退市日或者到期日")

  # Price information
  # 前收盘价格 - PreClose
  pre_close = Column(Float, comment="前收盘价格")
  # 前结算价格 - SettlementPrice
  settlement_price = Column(Float, comment="前结算价格")
  # 当日涨停价 - UpStopPrice
  up_stop_price = Column(Float, comment="当日涨停价")
  # 当日跌停价 - DownStopPrice
  down_stop_price = Column(Float, comment="当日跌停价")

  # Volume and share information
  # 流通股本 - FloatVolume
  float_volume = Column(Float, comment="流通股本")
  # 总股本 - TotalVolume
  total_volume = Column(Float, comment="总股本")
  # 自上市付息日起的累积未付利息额（债券） - AccumulatedInterest
  accumulated_interest = Column(
    Float(53), comment="自上市付息日起的累积未付利息额（债券）"
  )

  # Margin and trading parameters
  # 多头保证金率 - LongMarginRatio
  long_margin_ratio = Column(Float(53), comment="多头保证金率")
  # 空头保证金率 - ShortMarginRatio
  short_margin_ratio = Column(Float(53), comment="空头保证金率")
  # 最小变价单位 - PriceTick
  price_tick = Column(Float, comment="最小变价单位")
  # 合约乘数（对期货以外的品种，默认是1） - VolumeMultiple
  volume_multiple = Column(Integer, comment="合约乘数（对期货以外的品种，默认是1）")
  # 主力合约标记，1、2、3分别表示第一主力合约，第二主力合约，第三主力合约 - MainContract
  main_contract = Column(
    Integer,
    comment="主力合约标记，1、2、3分别表示第一主力合约，第二主力合约，第三主力合约",
  )

  # Order limits
  # 市价单最大下单量 - MaxMarketOrderVolume
  max_market_order_volume = Column(Integer, comment="市价单最大下单量")
  # 市价单最小下单量 - MinMarketOrderVolume
  min_market_order_volume = Column(Integer, comment="市价单最小下单量")
  # 限价单最大下单量 - MaxLimitOrderVolume
  max_limit_order_volume = Column(Integer, comment="限价单最大下单量")
  # 限价单最小下单量 - MinLimitOrderVolume
  min_limit_order_volume = Column(Integer, comment="限价单最小下单量")
  # 上期所大单边的处理算法 - MaxMarginSideAlgorithm
  max_margin_side_algorithm = Column(Integer, comment="上期所大单边的处理算法")

  # Trading status and characteristics
  # 自IPO起经历的交易日总数 - DayCountFromIPO
  day_count_from_ipo = Column(Integer, comment="自IPO起经历的交易日总数")
  # 昨日持仓量 - LastVolume
  last_volume = Column(Float, comment="昨日持仓量")
  # 合约停牌状态 - InstrumentStatus
  instrument_status = Column(Integer, comment="合约停牌状态")
  # 合约是否可交易 - IsTrading
  is_trading = Column(Boolean, comment="合约是否可交易")
  # 是否是近月合约 - IsRecent
  is_recent = Column(Boolean, comment="是否是近月合约")
  # 是否是连续合约 - IsContinuous
  is_continuous = Column(Boolean, comment="是否是连续合约")
  # 是否非盈利状态 - bNotProfitable
  b_not_profitable = Column(Boolean, comment="是否非盈利状态")
  # 是否同股不同权 - bDualClass
  b_dual_class = Column(Boolean, comment="是否同股不同权")
  # 连续合约类型 - ContinueType
  continue_type = Column(String(50), comment="连续合约类型")
  # 证券分类 - secuCategory
  secu_category = Column(Integer, comment="证券分类")
  # 证券属性 - secuAttri
  secu_attri = Column(Integer, comment="证券属性")

  # Additional order limits
  # 市价卖单最大单笔下单量 - MaxMarketSellOrderVolume
  max_market_sell_order_volume = Column(Integer, comment="市价卖单最大单笔下单量")
  # 市价卖单最小单笔下单量 - MinMarketSellOrderVolume
  min_market_sell_order_volume = Column(Integer, comment="市价卖单最小单笔下单量")
  # 限价卖单最大单笔下单量 - MaxLimitSellOrderVolume
  max_limit_sell_order_volume = Column(Integer, comment="限价卖单最大单笔下单量")
  # 限价卖单最小单笔下单量 - MinLimitSellOrderVolume
  min_limit_sell_order_volume = Column(Integer, comment="限价卖单最小单笔下单量")
  # 盘后定价委托数量的上限（买） - MaxFixedBuyOrderVol
  max_fixed_buy_order_vol = Column(Integer, comment="盘后定价委托数量的上限（买）")
  # 盘后定价委托数量的下限（买） - MinFixedBuyOrderVol
  min_fixed_buy_order_vol = Column(Integer, comment="盘后定价委托数量的下限（买）")
  # 盘后定价委托数量的上限（卖） - MaxFixedSellOrderVol
  max_fixed_sell_order_vol = Column(Integer, comment="盘后定价委托数量的上限（卖）")
  # 盘后定价委托数量的下限（卖） - MinFixedSellOrderVol
  min_fixed_sell_order_vol = Column(Integer, comment="盘后定价委托数量的下限（卖）")

  # Special attributes
  # 标识港股是否为沪港通或深港通标的证券 - HSGTFlag
  hsgt_flag = Column(Integer, comment="标识港股是否为沪港通或深港通标的证券")
  # 债券面值 - BondParValue
  bond_par_value = Column(Float(53), comment="债券面值")
  # 投资者适当性管理分类 - QualifiedType
  qualified_type = Column(Integer, comment="投资者适当性管理分类")
  # 价差类别（港股用），1-股票，3-债券，4-期权，5-交易所买卖基金 - PriceTickType
  price_tick_type = Column(
    Integer, comment="价差类别（港股用），1-股票，3-债券，4-期权，5-交易所买卖基金"
  )
  # 交易状态 - tradingStatus
  trading_status = Column(String(50), comment="交易状态")

  # Options related
  # 期权合约单位 - OptUnit
  opt_unit = Column(Float(53), comment="期权合约单位")
  # 期权单位保证金 - MarginUnit
  margin_unit = Column(Float(53), comment="期权单位保证金")
  # 期权标的证券代码或可转债正股标的证券代码 - OptUndlCode
  opt_undl_code = Column(String(50), comment="期权标的证券代码或可转债正股标的证券代码")
  # 期权标的证券市场或可转债正股标的证券市场 - OptUndlMarket
  opt_undl_market = Column(
    String(20), comment="期权标的证券市场或可转债正股标的证券市场"
  )
  # 期权整手数 - OptLotSize
  opt_lot_size = Column(Integer, comment="期权整手数")
  # 期权行权价或可转债转股价 - OptExercisePrice
  opt_exercise_price = Column(Float(53), comment="期权行权价或可转债转股价")
  # 全国股转转让类型 - NeeqExeType
  neeq_exe_type = Column(Integer, comment="全国股转转让类型")
  # 交易所期权合约保证金不变部分 - OptExchFixedMargin
  opt_exch_fixed_margin = Column(Float(53), comment="交易所期权合约保证金不变部分")
  # 交易所期权合约最小保证金 - OptExchMiniMargin
  opt_exch_mini_margin = Column(Float(53), comment="交易所期权合约最小保证金")

  # Currency and additional info
  # 币种 - Ccy
  ccy = Column(String(10), comment="币种")
  # IB安全类型，期货或股票 - IbSecType
  ib_sec_type = Column(String(50), comment="IB安全类型，期货或股票")
  # 期权标的无风险利率 - OptUndlRiskFreeRate
  opt_undl_risk_free_rate = Column(Float(53), comment="期权标的无风险利率")
  # 期权标的历史波动率 - OptUndlHistoryRate
  opt_undl_history_rate = Column(Float(53), comment="期权标的历史波动率")
  # 期权行权终止日 - EndDelivDate
  end_deliv_date = Column(String(24), comment="期权行权终止日")
  # 注册资本（单位:百万） - RegisteredCapital
  registered_capital = Column(Float, comment="注册资本（单位:百万）")
  # 最大有效申报范围 - MaxOrderPriceRange
  max_order_price_range = Column(Float(53), comment="最大有效申报范围")
  # 最小有效申报范围 - MinOrderPriceRange
  min_order_price_range = Column(Float(53), comment="最小有效申报范围")
  # 同股同权比例 - VoteRightRatio
  vote_right_ratio = Column(Float, comment="同股同权比例")

  # Repo limits
  # 最小回购天数 - m_nMinRepurchaseDaysLimit
  m_n_min_repurchase_days_limit = Column(Integer, comment="最小回购天数")
  # 最大回购天数 - m_nMaxRepurchaseDaysLimit
  m_n_max_repurchase_days_limit = Column(Integer, comment="最大回购天数")
  # 国债逆回购计息天数
  interest_accrual_days = Column(Integer, comment="国债逆回购计息天数")

  # Delivery info
  # 交割年份 - DeliveryYear
  delivery_year = Column(Integer, comment="交割年份")
  # 交割月 - DeliveryMonth
  delivery_month = Column(Integer, comment="交割月")
  # 标识期权，1-过期，2-当月，3-下月，4-下季，5-隔季，6-隔下季 - ContractType
  contract_type = Column(
    Integer, comment="标识期权，1-过期，2-当月，3-下月，4-下季，5-隔季，6-隔下季"
  )

  # Trading quotas
  # 期货品种交易配额 - ProductTradeQuota
  product_trade_quota = Column(Float, comment="期货品种交易配额")
  # 期货合约交易配额 - ContractTradeQuota
  contract_trade_quota = Column(Float, comment="期货合约交易配额")
  # 期货品种持仓配额 - ProductOpenInterestQuota
  product_open_interest_quota = Column(Float, comment="期货品种持仓配额")
  # 期货合约持仓配额 - ContractOpenInterestQuota
  contract_open_interest_quota = Column(Float, comment="期货合约持仓配额")

  # Fee and charge information
  # 期货和期权手续费方式，0-未知，1-按元/手，2-按费率 - ChargeType
  charge_type = Column(
    Integer, comment="期货和期权手续费方式，0-未知，1-按元/手，2-按费率"
  )
  # 开仓手续费率，-1表示没有 - ChargeOpen
  charge_open = Column(Float, comment="开仓手续费率，-1表示没有")
  # 平仓手续费率，-1表示没有 - ChargeClose
  charge_close = Column(Float, comment="平仓手续费率，-1表示没有")
  # 开今仓（日内开仓）手续费率，-1表示没有 - ChargeTodayOpen
  charge_today_open = Column(Float, comment="开今仓（日内开仓）手续费率，-1表示没有")
  # 平今仓（日内平仓）手续费率，-1表示没有 - ChargeTodayClose
  charge_today_close = Column(Float, comment="平今仓（日内平仓）手续费率，-1表示没有")

  # Option specific
  # 期权类型，-1为非期权，0为期权认购，1为期权认沽 - OptionType
  option_type = Column(
    Integer, comment="期权类型，-1为非期权，0为期权认购，1为期权认沽"
  )
  # 交割月持仓倍数 - OpenInterestMultiple
  open_interest_multiple = Column(Float, comment="交割月持仓倍数")

  def to_dict(self):
    """序列化为字典"""
    return {
      "id": self.id,
      "market": self.market,  # 修复：使用正确的字段名
      "code": self.code,  # 修复：使用正确的字段名
      "name": self.name,  # 修复：使用正确的字段名
      "type": self.type.value if self.type else None,
      "abbreviation": self.abbreviation,
      "product_id": self.product_id,
      "product_name": self.product_name,
      "underlying_code": self.underlying_code,
      "extend_name": self.extend_name,
      "exchange_code": self.exchange_code,
      "rzrk_code": self.rzrk_code,
      "uni_code": self.uni_code,
      "create_date": self.create_date.isoformat() if self.create_date else None,
      "open_date": self.open_date.isoformat() if self.open_date else None,
      "expire_date": self.expire_date.isoformat() if self.expire_date else None,
      "pre_close": self.pre_close,
      "settlement_price": self.settlement_price,
      "up_stop_price": self.up_stop_price,
      "down_stop_price": self.down_stop_price,
      "float_volume": self.float_volume,
      "total_volume": self.total_volume,
      "accumulated_interest": self.accumulated_interest,
      "long_margin_ratio": self.long_margin_ratio,
      "short_margin_ratio": self.short_margin_ratio,
      "price_tick": self.price_tick,
      "volume_multiple": self.volume_multiple,
      "main_contract": self.main_contract,
      "max_market_order_volume": self.max_market_order_volume,
      "min_market_order_volume": self.min_market_order_volume,
      "max_limit_order_volume": self.max_limit_order_volume,
      "min_limit_order_volume": self.min_limit_order_volume,
      "max_margin_side_algorithm": self.max_margin_side_algorithm,
      "day_count_from_ipo": self.day_count_from_ipo,
      "last_volume": self.last_volume,
      "instrument_status": self.instrument_status,
      "is_trading": self.is_trading,
      "is_recent": self.is_recent,
      "is_continuous": self.is_continuous,
      "b_not_profitable": self.b_not_profitable,
      "b_dual_class": self.b_dual_class,
      "continue_type": self.continue_type,
      "secu_category": self.secu_category,
      "secu_attri": self.secu_attri,
      "max_market_sell_order_volume": self.max_market_sell_order_volume,
      "min_market_sell_order_volume": self.min_market_sell_order_volume,
      "max_limit_sell_order_volume": self.max_limit_sell_order_volume,
      "min_limit_sell_order_volume": self.min_limit_sell_order_volume,
      "max_fixed_buy_order_vol": self.max_fixed_buy_order_vol,
      "min_fixed_buy_order_vol": self.min_fixed_buy_order_vol,
      "max_fixed_sell_order_vol": self.max_fixed_sell_order_vol,
      "min_fixed_sell_order_vol": self.min_fixed_sell_order_vol,
      "hsgt_flag": self.hsgt_flag,
      "bond_par_value": self.bond_par_value,
      "qualified_type": self.qualified_type,
      "price_tick_type": self.price_tick_type,
      "trading_status": self.trading_status,
      "opt_unit": self.opt_unit,
      "margin_unit": self.margin_unit,
      "opt_undl_code": self.opt_undl_code,
      "opt_undl_market": self.opt_undl_market,
      "opt_lot_size": self.opt_lot_size,
      "opt_exercise_price": self.opt_exercise_price,
      "neeq_exe_type": self.neeq_exe_type,
      "opt_exch_fixed_margin": self.opt_exch_fixed_margin,
      "opt_exch_mini_margin": self.opt_exch_mini_margin,
      "ccy": self.ccy,
      "ib_sec_type": self.ib_sec_type,
      "opt_undl_risk_free_rate": self.opt_undl_risk_free_rate,
      "opt_undl_history_rate": self.opt_undl_history_rate,
      "end_deliv_date": self.end_deliv_date.isoformat()
      if self.end_deliv_date
      else None,
      "registered_capital": self.registered_capital,
      "max_order_price_range": self.max_order_price_range,
      "min_order_price_range": self.min_order_price_range,
      "vote_right_ratio": self.vote_right_ratio,
      "m_n_min_repurchase_days_limit": self.m_n_min_repurchase_days_limit,
      "m_n_max_repurchase_days_limit": self.m_n_max_repurchase_days_limit,
      "interest_accrual_days": self.interest_accrual_days,
      "delivery_year": self.delivery_year,
      "delivery_month": self.delivery_month,
      "contract_type": self.contract_type,
      "product_trade_quota": self.product_trade_quota,
      "contract_trade_quota": self.contract_trade_quota,
      "product_open_interest_quota": self.product_open_interest_quota,
      "contract_open_interest_quota": self.contract_open_interest_quota,
      "charge_type": self.charge_type,
      "charge_open": self.charge_open,
      "charge_close": self.charge_close,
      "charge_today_open": self.charge_today_open,
      "charge_today_close": self.charge_today_close,
      "option_type": self.option_type,
      "open_interest_multiple": self.open_interest_multiple,
      "created_at": self.created_at.isoformat() if self.created_at else None,
      "updated_at": self.updated_at.isoformat() if self.updated_at else None,
    }
