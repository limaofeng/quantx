"""
模型枚举类型 - 业务相关的枚举定义
"""

from enum import Enum, IntEnum

import strawberry


class PriceType(Enum):
  """价格类型"""

  FIX_PRICE = 11
  """ 限价 """
  LATEST_PRICE = 5
  """ 按最新价成交 """
  MARKET_PEER_PRICE_FIRST = 44
  """ 对手方最优价 """
  MARKET_MINE_PRICE_FIRST = 45
  """ 本方最优价 """
  MARKET_CONVERT_5_LIMIT = 40
  """ 最优五档即时成交剩余转限价 """

  def __str__(self):
    return self.name.lower()


# 报价类型
class OrderPriceType(IntEnum):
  """价格类型"""

  ANY = 49  # 市价
  """ 市价 """
  LIMIT = 50  # 限价
  """ 限价 """
  BEST = 51  # 最优价
  """ 最优价 """
  PROP_ALLOTMENT = 52  # 配股
  """ 配股 """
  PROP_REFER = 53  # 转托
  """ 转托 """
  PROP_SUBSCRIBE = 54  # 申购
  """ 申购 """
  PROP_BUYBACK = 55  # 回购
  """ 回购 """
  PROP_PLACING = 56  # 配售
  """ 配售 """
  PROP_DECIDE = 57  # 指定
  """ 指定 """
  PROP_EQUITY = 58  # 转股
  """ 转股 """
  PROP_SELLBACK = 59  # 回售
  """ 回售 """
  PROP_DIVIDEND = 60  # 股息
  """ 股息 """
  PROP_SHENZHEN_PLACING = 68  # 深圳配售确认
  """ 深圳配售确认 """
  PROP_CANCEL_PLACING = 69  # 配售放弃
  """ 配售放弃 """
  PROP_WDZY = 70  # 无冻质押
  """ 无冻质押 """
  PROP_DJZY = 71  # 冻结质押
  """ 冻结质押 """
  PROP_WDJY = 72  # 无冻解押
  """ 无冻解押 """
  PROP_JDJY = 73  # 解冻解押
  """ 解冻解押 """
  PROP_ETF = 81  # ETF申购
  """ ETF申购 """
  PROP_VOTE = 75  # 投票
  """ 投票 """
  PROP_YYSGYS = 92  # 要约收购预售
  """ 要约收购预售 """
  PROP_YSYYJC = 77  # 预售要约解除
  """ 预售要约解除 """
  PROP_FUND_DEVIDEND = 78  # 基金设红
  """ 基金设红 """
  PROP_FUND_ENTRUST = 79  # 基金申赎
  """ 基金申赎 """
  PROP_CROSS_MARKET = 80  # 跨市转托
  """ 跨市转托 """
  PROP_EXERCIS = 83  # 权证行权
  """ 权证行权 """
  PROP_PEER_PRICE_FIRST = 84  # 对手方最优价格
  """ 对手方最优价格 """
  PROP_L5_FIRST_LIMITPX = 85  # 最优五档即时成交剩余转限价
  """ 最优五档即时成交剩余转限价 """
  PROP_MIME_PRICE_FIRST = 86  # 本方最优价格
  """ 本方最优价格 """
  PROP_INSTBUSI_RESTCANCEL = 87  # 即时成交剩余撤销
  """ 即时成交剩余撤销 """
  PROP_L5_FIRST_CANCEL = 88  # 最优五档即时成交剩余撤销
  """ 最优五档即时成交剩余撤销 """
  PROP_FULL_REAL_CANCEL = 89  # 全额成交并撤单
  """ 全额成交并撤单 """
  PROP_DIRECT_SECU_REPAY = 101  # 直接还券
  """ 直接还券 """
  PROP_FUND_CHAIHE = 90  # 基金拆合
  """ 基金拆合 """
  PROP_DEBT_CONVERSION = 91  # 债转股
  """ 债转股 """
  BID_LIMIT = 92  # 港股通竞价限价
  """ 港股通竞价限价 """
  ENHANCED_LIMIT = 93  # 港股通增强限价
  """ 港股通增强限价 """
  RETAIL_LIMIT = 94  # 港股通零股限价
  """ 港股通零股限价 """
  PROP_INCREASE_SHARE = 106  # 增发
  """ 增发 """
  PROP_COLLATERAL_TRANSFER = 107  # 担保品划转
  """ 担保品划转 """
  PROP_NEEQ_PRICING = 119  # 定价（全国股转 - 挂牌公司交易 - 协议转让）
  """ 定价（全国股转 - 挂牌公司交易 - 协议转让） """
  PROP_NEEQ_MATCH_CONFIRM = 120  # 成交确认（全国股转 - 挂牌公司交易 - 协议转让）
  """ 成交确认（全国股转 - 挂牌公司交易 - 协议转让） """
  PROP_NEEQ_MUTUAL_MATCH_CONFIRM = (
    121  # 互报成交确认（全国股转 - 挂牌公司交易 - 协议转让）
  )
  """ 互报成交确认（全国股转 - 挂牌公司交易 - 协议转让） """
  PROP_NEEQ_LIMIT = (
    122  # 限价（用于挂牌公司交易 - 做市转让 - 限价买卖和两网及退市交易-限价买卖）
  )
  """ 限价（用于挂牌公司交易 - 做市转让 - 限价买卖和两网及退市交易-限价买卖） """


@strawberry.enum(
  description="""委托类型

- BUY: 买入 - 买入股票的委托类型
- SELL: 卖出 - 卖出股票的委托类型"""
)
class OrderType(IntEnum):
  """委托类型枚举"""

  BUY = 23
  """买入 - 买入股票的委托类型"""

  SELL = 24
  """卖出 - 卖出股票的委托类型"""


class OrderStatus(IntEnum):
  """订单状态枚举 - 定义订单的各种状态"""

  UNREPORTED = 48  # 未报
  """未报 - 订单尚未上报"""

  WAIT_REPORTING = 49  # 待报
  """待报 - 订单等待上报"""

  REPORTED = 50  # 已报
  """已报 - 订单已上报"""

  REPORTED_CANCEL = 51  # 已报待撤
  """已报待撤 - 已上报的订单等待撤单"""

  PARTSUCC_CANCEL = 52  # 部成待撤
  """部成待撤 - 部分成交的订单等待撤单"""

  PART_CANCEL = 53  # 部撤
  """部撤 - 部分撤单"""

  CANCELED = 54  # 已撤
  """已撤 - 订单已撤单"""

  PART_SUCC = 55  # 部成
  """部成 - 订单部分成交"""

  SUCCEEDED = 56  # 已成
  """已成 - 订单已全部成交"""

  JUNK = 57  # 废单
  """废单 - 无效订单"""

  UNKNOWN = 255  # 未知
  """未知 - 订单状态未知"""


@strawberry.enum(
  description="""策略运行模式

- BACKTEST: 回测模式 - 使用历史数据进行策略回测
- PAPER: 模拟盘 - 使用实时数据进行虚拟交易（Paper Trading）
- LIVE: 实盘模式 - 真实交易环境（Live Trading）"""
)
class StrategyRunMode(str, Enum):
  """策略运行模式"""

  BACKTEST = "backtest"
  """回测模式 - 使用历史数据进行策略回测"""

  PAPER = "paper"
  """模拟盘（Paper Trading）- 使用实时数据进行虚拟交易，不涉及真实资金"""

  LIVE = "live"
  """实盘（Live Trading）- 真实交易环境，使用真实资金"""


@strawberry.enum(
  description="""策略标的范围

- SINGLE: 单标的 - 仅支持单只股票/标的
- MULTI: 多标的 - 支持多只股票/标的"""
)
class StrategyInstrumentScope(str, Enum):
  """策略标的范围（用于 Strategy 模型展示）"""

  SINGLE = "single"
  """单标的"""

  MULTI = "multi"
  """多标的"""


@strawberry.enum(
  description="""策略标的池来源

- STATIC: 创建运行实例时固定指定标的
- ACCOUNT_HOLDINGS: 由账户持仓快照动态维护标的池
- RADAR_CANDIDATES: 由 Engine 打板雷达协调候选标的池"""
)
class StrategyInstrumentUniverseMode(str, Enum):
  """策略运行实例的标的池维护方式。"""

  STATIC = "static"
  """创建实例时固定指定标的。"""

  ACCOUNT_HOLDINGS = "account_holdings"
  """由账户持仓快照动态维护标的。"""

  RADAR_CANDIDATES = "radar_candidates"
  """由 Engine 打板雷达协调候选标的。"""


@strawberry.enum(
  description="""策略模板状态

- ACTIVE: 激活 - 策略可用，可创建运行实例
- UPGRADING: 待升级 - 策略代码已更新，待确认升级
- DEPRECATED: 已弃用 - 策略已删除，不可创建新实例"""
)
class StrategyStatus(str, Enum):
  """策略模板状态（用于 Strategy 模型）"""

  ACTIVE = "active"
  """激活 - 策略可用，可创建运行实例"""

  UPGRADING = "upgrading"
  """待升级 - 策略代码已更新，待确认升级"""

  DEPRECATED = "deprecated"
  """已弃用 - 策略已删除，不可创建新实例"""


@strawberry.enum(
  description="""策略运行状态

- PENDING: 待启动 - 策略实例已创建但尚未开始运行
- RUNNING: 运行中 - 策略正在执行
- PAUSED: 暂停 - 策略运行已暂停
- STOPPED: 已停止 - 策略运行已手动停止
- COMPLETED: 已完成 - 策略运行正常结束（如回测完成）
- ERROR: 错误 - 策略运行出错"""
)
class StrategyRunStatus(str, Enum):
  """策略运行状态（用于 StrategyRun 模型）"""

  PENDING = "pending"
  """待启动"""

  RUNNING = "running"
  """运行中"""

  PAUSED = "paused"
  """暂停"""

  STOPPED = "stopped"
  """已停止"""

  COMPLETED = "completed"
  """已完成（回测）"""

  ERROR = "error"
  """错误"""


@strawberry.enum(
  description="""策略分类

- TREND_FOLLOWING: 趋势跟随 - 跟随市场趋势方向进行交易
- MEAN_REVERSION: 均值回归 - 基于价格向均值回归的特性进行交易
- ARBITRAGE: 套利策略 - 利用价格差异进行无风险套利
- MARKET_MAKING: 做市策略 - 提供流动性并赚取买卖价差
- OTHER: 其他策略 - 不属于上述分类的策略"""
)
class StrategyCategory(str, Enum):
  """策略分类"""

  TREND_FOLLOWING = "trend_following"
  """趋势跟随 - 跟随市场趋势方向进行交易"""

  MEAN_REVERSION = "mean_reversion"
  """均值回归 - 基于价格向均值回归的特性进行交易"""

  ARBITRAGE = "arbitrage"
  """套利策略 - 利用价格差异进行无风险套利"""

  MARKET_MAKING = "market_making"
  """做市策略 - 提供流动性并赚取买卖价差"""

  OTHER = "other"
  """其他策略 - 不属于上述分类的策略"""


@strawberry.enum(
  description="""策略风险等级

- LOW: 低风险 - 保守型策略,适合风险厌恶型投资者
- MEDIUM: 中风险 - 平衡型策略,风险和收益相对均衡
- HIGH: 高风险 - 激进型策略,追求高收益但承担较高风险
- VERY_HIGH: 极高风险 - 高杠杆或高频策略,需要专业投资者"""
)
class RiskLevel(str, Enum):
  """策略风险等级"""

  LOW = "low"
  """低风险 - 保守型策略,适合风险厌恶型投资者"""

  MEDIUM = "medium"
  """中风险 - 平衡型策略,风险和收益相对均衡"""

  HIGH = "high"
  """高风险 - 激进型策略,追求高收益但承担较高风险"""

  VERY_HIGH = "very_high"
  """极高风险 - 高杠杆或高频策略,需要专业投资者"""


@strawberry.enum(
  description="""Sell reason for strategy exits.

- STOP_LOSS: Exit by stop loss.
- STRUCTURE_BREAK: Exit due to structure breakdown.
- TIME_STOP: Exit after holding too long without profit.
- TAKE_PROFIT: Exit by take profit.
- REBALANCE: Exit for rebalancing.
- RISK_CONTROL: Exit triggered by risk control."""
)
class SellReason(str, Enum):
  """Strategy sell reason"""

  STOP_LOSS = "stop_loss"
  STRUCTURE_BREAK = "structure_break"
  TIME_STOP = "time_stop"
  TAKE_PROFIT = "take_profit"
  REBALANCE = "rebalance"
  RISK_CONTROL = "risk_control"


@strawberry.enum(
  description="""Risk control level for strategy execution.

- NORMAL: No restrictions.
- REDUCE: Reduce position sizing.
- STOP_OPEN: Stop opening new positions.
- STOP_ALL: Stop opening trades; allow exits only.
- LIQUIDATE: Liquidate all positions."""
)
class RiskControlLevel(str, Enum):
  """Strategy risk control level"""

  NORMAL = "normal"
  REDUCE = "reduce"
  STOP_OPEN = "stop_open"
  STOP_ALL = "stop_all"
  LIQUIDATE = "liquidate"


# 金融产品类型
@strawberry.enum(
  description="""金融产品类型

- INDEX: 指数 - 股票市场的一个重要组成部分
- STOCK: 股票 - 代表公司所有权的金融工具
- FUND: 基金 - 由多个投资者共同投资的集合投资工具
- ETF: 交易型开放式指数基金
- TRR: 国债逆回购 - 一种短期融资工具"""
)
class InstrumentType(str, Enum):
  """金融产品类型"""

  INDEX = strawberry.enum_value(
    "index", description="指数 - 股票市场的一个重要组成部分"
  )
  """指数 - 股票市场的一个重要组成部分"""

  STOCK = strawberry.enum_value("stock", description="股票 - 代表公司所有权的金融工具")
  """股票 - 代表公司所有权的金融工具"""

  FUND = strawberry.enum_value(
    "fund", description="基金 - 由多个投资者共同投资的集合投资工具"
  )
  """基金 - 由多个投资者共同投资的集合投资工具"""

  ETF = strawberry.enum_value("etf", description="交易型开放式指数基金")
  """ETF - 交易型开放式指数基金"""

  TRR = strawberry.enum_value("trr", description="国债逆回购 - 一种短期融资工具")
  """国债逆回购 - 一种短期融资工具"""


class AccountType(str, Enum):
  STOCK = "STOCK"  # 股票账户
  HUGANGTONG = "HUGANGTONG"  # 沪港通账户
  CREDIT = "CREDIT"  # 信用账户
  FUTURE = "FUTURE"  # 期货账户
  SHENGANGTONG = "SHENGANGTONG"  # 深港通账户

  @staticmethod
  def from_int(value: int) -> "AccountType":
    mapping = {
      2: AccountType.STOCK,
      7: AccountType.HUGANGTONG,
      3: AccountType.CREDIT,
      1: AccountType.FUTURE,
      11: AccountType.SHENGANGTONG,
    }
    return mapping.get(value, None)

  def to_int(self) -> int:
    return {
      AccountType.STOCK: 2,
      AccountType.HUGANGTONG: 7,
      AccountType.CREDIT: 3,
      AccountType.FUTURE: 1,
      AccountType.SHENGANGTONG: 11,
    }.get(self, -1)


# 账户状态
class AccountStatus(int, Enum):
  """账户状态"""

  INVALID = -1  # 无效
  """无效"""
  OK = 0  # 正常
  """正常"""
  WAITING_LOGIN = 1  # 连接中
  """连接中"""
  LOGGING_IN = 2  # 登陆中
  """登陆中"""
  LOGIN_FAILED = 3  # 失败
  """失败"""
  INITIALIZING = 4  # 初始化中
  """初始化中"""
  CORRECTING = 5  # 数据刷新校正中
  """数据刷新校正中"""
  CLOSED = 6  # 收盘后
  """收盘后"""
  ASSIST_FAIL = 7  # 穿透副链接断开
  """穿透副链接断开"""
  DISABLE_BY_SYS = 8  # 系统停用（总线使用-密码错误超限）
  """系统停用（总线使用-密码错误超限）"""
  DISABLE_BY_USER = 9  # 用户停用（总线使用）
  """用户停用（总线使用）"""
