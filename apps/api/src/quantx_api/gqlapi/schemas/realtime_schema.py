import asyncio
import logging
from datetime import datetime
from functools import wraps
from typing import AsyncIterator, List, Optional

import strawberry
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.enums import OrderStatus
from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.tick import Tick
from quantx_infrastructure.services.limit_up_board_assistant_projection_service import (
  limit_up_board_assistant_projection_service,
)
from quantx_infrastructure.services.limit_up_board_replay_projection_service import (
  limit_up_board_replay_projection_service,
)
from quantx_infrastructure.services.order_service import OrderService
from quantx_infrastructure.services.runtime_subscription_bridge import (
  runtime_subscription_bridge,
)
from quantx_infrastructure.services.t_trade_monitor_projection_service import (
  t_trade_monitor_projection_service,
)
from sqlalchemy import select

from quantx_api.gqlapi.security import authorized_account_id
from quantx_api.gqlapi.types.limit_up_board_assistant_types import (
  LimitUpBoardAssistantUpdateNotice,
)
from quantx_api.gqlapi.types.limit_up_board_replay_types import (
  LimitUpBoardReplayUpdateKind,
  LimitUpBoardReplayUpdateNotice,
)
from quantx_api.gqlapi.types.strategy_subscription_types import (
  LogLevel,
  StrategyKLineData,
  StrategyLogEntry,
  StrategyMarketDataEvent,
  StrategyTickData,
)
from quantx_api.gqlapi.types.strategy_types import StrategyInstanceEvent
from quantx_api.gqlapi.types.t_trade_types import TTradeUpdateNotice
from quantx_api.runtime_status import component_status, required_components

from ..security import principal_from_context
from ..types import (
  DeploymentFlowRun,
  KLineData,
  LogLine,
  MarketDepth,
  Order,
  OrderEvent,
  RealTimePrice,
  StrategyStatusInfo,
  SystemAlert,
  TickData,
  TradingEventType,
)

logger = logging.getLogger(__name__)

FLOW_RUN_LOG_POLL_SECONDS = 1.0
LIVE_FLOW_RUN_STATES = {
  "PENDING",
  "SCHEDULED",
  "RUNNING",
  "LATE",
  "PAUSED",
  "CANCELLING",
}


def _flow_run_log_key(log) -> tuple[str, int, str]:
  timestamp = getattr(log, "timestamp", None) or getattr(log, "time", None)
  timestamp_key = timestamp.isoformat() if timestamp else ""
  return (
    timestamp_key,
    int(getattr(log, "level", 0) or 0),
    str(getattr(log, "message", "") or ""),
  )


def _to_graphql_log_line(log) -> LogLine:
  timestamp = (
    getattr(log, "timestamp", None)
    or getattr(log, "time", None)
    or time_utils.now()
  )
  return LogLine(
    time=timestamp,
    level=int(getattr(log, "level", 0) or 0),
    message=str(getattr(log, "message", "") or ""),
  )


def _is_live_flow_run_state(state: Optional[str]) -> bool:
  return (state or "").upper() in LIVE_FLOW_RUN_STATES


class SubscriptionError(Exception):
  """订阅相关错误的基类"""

  def __init__(self, message: str, error_code: str = "SUBSCRIPTION_ERROR"):
    self.message = message
    self.error_code = error_code
    super().__init__(message)


class DataSourceError(SubscriptionError):
  """数据源错误"""

  def __init__(self, message: str):
    super().__init__(message, "DATA_SOURCE_ERROR")


class ValidationError(SubscriptionError):
  """参数验证错误"""

  def __init__(self, message: str):
    super().__init__(message, "VALIDATION_ERROR")


def with_error_handling(func):
  """订阅函数错误处理装饰器"""

  @wraps(func)
  async def wrapper(*args, **kwargs):
    try:
      async for item in func(*args, **kwargs):
        yield item
    except SubscriptionError:
      raise  # 重新抛出已知的订阅错误
    except Exception as e:
      logger.error(f"订阅函数 {func.__name__} 发生未知错误: {e}")
      raise SubscriptionError(f"订阅服务暂时不可用: {str(e)}")

  return wrapper


def with_retry(max_retries: int = 3, retry_delay: float = 1.0):
  """重试装饰器"""

  def decorator(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
      last_exception = None
      for attempt in range(max_retries + 1):
        try:
          async for item in func(*args, **kwargs):
            yield item
          return  # 成功执行，退出重试循环
        except Exception as e:
          last_exception = e
          if attempt < max_retries:
            logger.warning(
              f"订阅函数 {func.__name__} 第 {attempt + 1} 次尝试失败，{retry_delay}秒后重试: {e}"
            )
            await asyncio.sleep(retry_delay)
          else:
            logger.error(f"订阅函数 {func.__name__} 在 {max_retries} 次重试后仍然失败")
            raise last_exception

    return wrapper

  return decorator


@strawberry.type(description="实时数据订阅")
class RealtimeSubscription:
  @strawberry.subscription(description="订阅首板晋级工作台版本通知")
  async def first_board_promotion_updates(
    self,
    info: strawberry.types.Info,
    account_id: str,
  ) -> AsyncIterator[LimitUpBoardAssistantUpdateNotice]:
    authorized = authorized_account_id(info, account_id)
    async for message in limit_up_board_assistant_projection_service.subscribe(
      authorized
    ):
      yield LimitUpBoardAssistantUpdateNotice(
        account_id=authorized,
        version=str(message.get("version") or "0"),
        occurred_at=datetime.fromisoformat(
          str(message.get("occurred_at")).replace("Z", "+00:00")
        ),
      )

  @strawberry.subscription(description="订阅账户级打板助手投影更新")
  async def limit_up_board_assistant_updates(
    self,
    info: strawberry.types.Info,
    account_id: str,
  ) -> AsyncIterator[LimitUpBoardAssistantUpdateNotice]:
    authorized = authorized_account_id(info, account_id)
    async for message in limit_up_board_assistant_projection_service.subscribe(
      authorized
    ):
      yield LimitUpBoardAssistantUpdateNotice(
        account_id=authorized,
        version=str(message.get("version") or "0"),
        occurred_at=datetime.fromisoformat(
          str(message.get("occurred_at")).replace("Z", "+00:00")
        ),
      )

  @strawberry.subscription(description="订阅账户级打板助手历史回放更新")
  async def limit_up_board_replay_updates(
    self,
    info: strawberry.types.Info,
    account_id: str,
  ) -> AsyncIterator[LimitUpBoardReplayUpdateNotice]:
    authorized = authorized_account_id(info, account_id)
    async for message in limit_up_board_replay_projection_service.subscribe(
      authorized
    ):
      yield LimitUpBoardReplayUpdateNotice(
        account_id=authorized,
        job_id=str(message.get("job_id") or ""),
        revision=str(message.get("revision") or "0"),
        kind=LimitUpBoardReplayUpdateKind(
          str(message.get("kind") or "PROGRESS")
        ),
        occurred_at=datetime.fromisoformat(
          str(message.get("occurred_at")).replace("Z", "+00:00")
        ),
      )

  @strawberry.subscription(description="订阅账户做 T 监控投影更新")
  async def t_trade_updates(
    self,
    info: strawberry.types.Info,
    account_id: str,
  ) -> AsyncIterator[TTradeUpdateNotice]:
    authorized = authorized_account_id(info, account_id)
    async for message in t_trade_monitor_projection_service.subscribe(authorized):
      yield TTradeUpdateNotice(
        account_id=authorized,
        version=str(message.get("version") or "0"),
        occurred_at=datetime.fromisoformat(
          str(message.get("occurred_at")).replace("Z", "+00:00")
        ),
      )

  @strawberry.subscription(description="订阅股票实时报价流")
  @with_error_handling
  @with_retry(max_retries=2, retry_delay=2.0)
  async def market_quotes(self, stock_list: List[str]) -> AsyncIterator[RealTimePrice]:
    """
    订阅股票实时报价数据
    支持多股票订阅

    Args:
        stock_list: 股票代码列表,如 ["000001.SZ", "600000.SH"]
    """
    # 参数验证
    if not stock_list:
      raise ValidationError("股票代码列表不能为空")

    if len(stock_list) > 100:
      raise ValidationError("单次订阅股票数量不能超过100个")

    try:
      # 合并多个股票的订阅
      async def merge_subscriptions():
        subscriptions = {}  # symbol -> (iterator, task)
        task_to_symbol = {}  # task -> symbol 反向索引，用于O(1)查找

        async def cleanup_pending():
          if not subscriptions:
            return

          # 取消所有未完成任务
          pending_tasks = [
            task for _, task in subscriptions.values() if not task.done()
          ]
          for task in pending_tasks:
            task.cancel()

          if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

          # 主动关闭异步生成器，触发底层退订
          for symbol, (async_iter, _) in list(subscriptions.items()):
            try:
              await async_iter.aclose()
            except Exception as cleanup_err:
              logger.warning(f"关闭股票 {symbol} 订阅生成器失败: {cleanup_err}")

          subscriptions.clear()
          task_to_symbol.clear()

        try:
          # 初始化订阅
          for symbol in stock_list:
            async_iter = runtime_subscription_bridge.subscribe_price(symbol)
            task = asyncio.create_task(async_iter.__anext__())
            subscriptions[symbol] = (async_iter, task)
            task_to_symbol[task] = symbol

          while subscriptions:
            # 等待任意任务完成
            done, _ = await asyncio.wait(
              [task for _, task in subscriptions.values()],
              return_when=asyncio.FIRST_COMPLETED,
            )

            for completed_task in done:
              symbol = task_to_symbol[completed_task]  # O(1) 查找
              async_iter, _ = subscriptions[symbol]

              try:
                result = await completed_task
                yield RealTimePrice.from_domain_realtime_price(result)

                # 创建新任务继续订阅
                new_task = asyncio.create_task(async_iter.__anext__())
                subscriptions[symbol] = (async_iter, new_task)
                task_to_symbol[new_task] = symbol
                del task_to_symbol[completed_task]

              except StopAsyncIteration:
                del subscriptions[symbol]
                del task_to_symbol[completed_task]
              except Exception as e:
                logger.error(f"订阅股票 {symbol} 报价时出错: {e}")
                try:
                  await async_iter.aclose()
                except Exception as close_err:
                  logger.warning(f"关闭股票 {symbol} 报价生成器异常: {close_err}")
                del subscriptions[symbol]
                del task_to_symbol[completed_task]
                if "connection" in str(e).lower() or "timeout" in str(e).lower():
                  raise DataSourceError(f"数据源连接异常: {e}")
        finally:
          await cleanup_pending()

      async for price_data in merge_subscriptions():
        yield price_data

    except Exception as e:
      logger.error(f"市场报价订阅出错: {e}")
      raise

  @strawberry.subscription(description="订阅K线数据流")
  @with_error_handling
  async def market_klines(
    self, stock_list: List[str], periods: List[str]
  ) -> AsyncIterator[KLineData]:
    """
    订阅K线数据，支持多股票、多周期

    Args:
        stock_list: 股票代码列表,如 ["000001.SZ", "600000.SH"]
        periods: K线周期列表,如 ["1m", "5m", "1d"]
    """
    # 参数验证
    if not stock_list:
      raise ValidationError("股票代码列表不能为空")

    if not periods:
      raise ValidationError("K线周期列表不能为空")

    valid_periods = ["1m", "5m", "15m", "30m", "1h", "1d"]

    # 验证周期
    for period in periods:
      if period not in valid_periods:
        raise ValidationError(f"无效的K线周期: {period}. 支持的周期: {valid_periods}")

    try:

      async def merge_subscriptions():
        subscriptions = {}  # key -> (iterator, task)
        task_to_key = {}  # task -> key 反向索引，用于O(1)查找

        async def cleanup_pending():
          if not subscriptions:
            return

          pending_tasks = [
            task for _, task in subscriptions.values() if not task.done()
          ]
          for task in pending_tasks:
            task.cancel()

          if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

          for key, (async_iter, _) in list(subscriptions.items()):
            try:
              await async_iter.aclose()
            except Exception as cleanup_err:
              logger.warning(f"关闭K线订阅 {key} 生成器失败: {cleanup_err}")

          subscriptions.clear()
          task_to_key.clear()

        try:
          # 初始化订阅
          for symbol in stock_list:
            for period in periods:
              key = f"{symbol}_{period}"
              async_iter = runtime_subscription_bridge.subscribe_kline(symbol, period)
              task = asyncio.create_task(async_iter.__anext__())
              subscriptions[key] = (async_iter, task)
              task_to_key[task] = key

          while subscriptions:
            done, _ = await asyncio.wait(
              [task for _, task in subscriptions.values()],
              return_when=asyncio.FIRST_COMPLETED,
            )

            for completed_task in done:
              key = task_to_key[completed_task]  # O(1) 查找
              async_iter, _ = subscriptions[key]

              try:
                result = await completed_task
                yield KLineData.from_kline(result)

                # 创建新任务继续订阅
                new_task = asyncio.create_task(async_iter.__anext__())
                subscriptions[key] = (async_iter, new_task)
                task_to_key[new_task] = key
                del task_to_key[completed_task]

              except StopAsyncIteration:
                del subscriptions[key]
                del task_to_key[completed_task]
              except Exception as e:
                logger.error(f"订阅K线 {key} 时出错: {e}")
                try:
                  await async_iter.aclose()
                except Exception as close_err:
                  logger.warning(f"关闭K线订阅 {key} 生成器异常: {close_err}")
                del subscriptions[key]
                del task_to_key[completed_task]
        finally:
          await cleanup_pending()

      async for kline_data in merge_subscriptions():
        yield kline_data

    except Exception as e:
      logger.error(f"K线数据订阅出错: {e}")
      raise

  @strawberry.subscription(description="订阅市场深度数据流")
  async def market_depth(
    self, stock_list: List[str], levels: Optional[int] = 5
  ) -> AsyncIterator[MarketDepth]:
    """
    订阅市场深度数据，支持多股票、档位控制

    Args:
        stock_list: 股票代码列表,如 ["000001.SZ", "600000.SH"]
        levels: 深度档位数量,默认5档
    """
    try:

      async def merge_subscriptions():
        subscriptions = {}  # symbol -> (iterator, task)
        task_to_symbol = {}  # task -> symbol 反向索引，用于O(1)查找

        async def cleanup_pending():
          if not subscriptions:
            return

          pending_tasks = [
            task for _, task in subscriptions.values() if not task.done()
          ]
          for task in pending_tasks:
            task.cancel()

          if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

          for symbol, (async_iter, _) in list(subscriptions.items()):
            try:
              await async_iter.aclose()
            except Exception as cleanup_err:
              logger.warning(f"关闭市场深度 {symbol} 生成器失败: {cleanup_err}")

          subscriptions.clear()
          task_to_symbol.clear()

        try:
          # 初始化订阅
          for symbol in stock_list:
            async_iter = runtime_subscription_bridge.subscribe_depth(symbol)
            task = asyncio.create_task(async_iter.__anext__())
            subscriptions[symbol] = (async_iter, task)
            task_to_symbol[task] = symbol

          while subscriptions:
            done, _ = await asyncio.wait(
              [task for _, task in subscriptions.values()],
              return_when=asyncio.FIRST_COMPLETED,
            )

            for completed_task in done:
              symbol = task_to_symbol[completed_task]  # O(1) 查找
              async_iter, _ = subscriptions[symbol]

              try:
                result = await completed_task
                # 根据参数限制档位数量
                if levels:
                  result.bid_levels = result.bid_levels[:levels]
                  result.ask_levels = result.ask_levels[:levels]

                yield MarketDepth.from_domain_market_depth(result)

                # 创建新任务继续订阅
                new_task = asyncio.create_task(async_iter.__anext__())
                subscriptions[symbol] = (async_iter, new_task)
                task_to_symbol[new_task] = symbol
                del task_to_symbol[completed_task]

              except StopAsyncIteration:
                del subscriptions[symbol]
                del task_to_symbol[completed_task]
              except Exception as e:
                logger.error(f"订阅市场深度 {symbol} 时出错: {e}")
                try:
                  await async_iter.aclose()
                except Exception as close_err:
                  logger.warning(f"关闭市场深度 {symbol} 生成器异常: {close_err}")
                del subscriptions[symbol]
                del task_to_symbol[completed_task]
        finally:
          await cleanup_pending()

      async for depth_data in merge_subscriptions():
        yield depth_data

    except Exception as e:
      logger.error(f"市场深度订阅出错: {e}")
      raise

  @strawberry.subscription(description="订阅逐笔成交数据流")
  async def market_ticks(self, stock_list: List[str]) -> AsyncIterator[TickData]:
    """
    订阅逐笔成交数据，支持多股票

    Args:
        stock_list: 股票代码列表,如 ["000001.SZ", "600000.SH"]
    """
    try:

      async def merge_subscriptions():
        subscriptions = {}  # symbol -> (iterator, task)
        task_to_symbol = {}  # task -> symbol 反向索引，用于O(1)查找

        async def cleanup_pending():
          if not subscriptions:
            return

          pending_tasks = [
            task for _, task in subscriptions.values() if not task.done()
          ]
          for task in pending_tasks:
            task.cancel()

          if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

          for symbol, (async_iter, _) in list(subscriptions.items()):
            try:
              await async_iter.aclose()
            except Exception as cleanup_err:
              logger.warning(f"关闭逐笔数据 {symbol} 生成器失败: {cleanup_err}")

          subscriptions.clear()
          task_to_symbol.clear()

        try:
          # 初始化订阅
          for symbol in stock_list:
            async_iter = runtime_subscription_bridge.subscribe_tick(symbol)
            task = asyncio.create_task(async_iter.__anext__())
            subscriptions[symbol] = (async_iter, task)
            task_to_symbol[task] = symbol

          while subscriptions:
            done, _ = await asyncio.wait(
              [task for _, task in subscriptions.values()],
              return_when=asyncio.FIRST_COMPLETED,
            )

            for completed_task in done:
              symbol = task_to_symbol[completed_task]  # O(1) 查找
              async_iter, _ = subscriptions[symbol]

              try:
                result = await completed_task
                yield TickData.from_tick(result)

                # 创建新任务继续订阅
                new_task = asyncio.create_task(async_iter.__anext__())
                subscriptions[symbol] = (async_iter, new_task)
                task_to_symbol[new_task] = symbol
                del task_to_symbol[completed_task]

              except StopAsyncIteration:
                del subscriptions[symbol]
                del task_to_symbol[completed_task]
              except Exception as e:
                logger.error(f"订阅逐笔数据 {symbol} 时出错: {e}")
                try:
                  await async_iter.aclose()
                except Exception as close_err:
                  logger.warning(f"关闭逐笔数据 {symbol} 生成器异常: {close_err}")
                del subscriptions[symbol]
                del task_to_symbol[completed_task]
        finally:
          await cleanup_pending()

      async for tick_data in merge_subscriptions():
        yield tick_data

    except Exception as e:
      logger.error(f"逐笔数据订阅出错: {e}")
      raise

  @strawberry.subscription(description="订阅交易事件流")
  @with_error_handling
  async def trading_events(
    self,
    event_types: Optional[List[TradingEventType]] = None,
    stock_codes: Optional[List[str]] = None,
    strategy_names: Optional[List[str]] = None,
  ) -> AsyncIterator[OrderEvent]:
    """
    订阅交易事件流 (个人量化软件专用)

    支持订单事件 (4种核心事件: 创建、成交、撤销、拒绝)

    Args:
        event_types: 事件类型过滤列表,不填则订阅所有事件
        stock_codes: 股票代码过滤列表 (用于自选股监控)
        strategy_names: 策略名称过滤列表 (用于策略监控)

    Returns:
        订单事件流 (OrderEvent)

    示例:
        # 订阅所有交易事件
        subscription {
          tradingEvents {
            eventType
            time
            order { id stockCode status }
            changes
          }
        }

        # 只订阅订单成交事件
        subscription {
          tradingEvents(eventTypes: [ORDER_FILLED, ORDER_CANCELLED]) {
            order { id stockCode status }
            changes
          }
        }

        # 监控自选股
        subscription {
          tradingEvents(stockCodes: ["600000.SH", "000001.SZ"]) {
            order { id stockCode status }
            changes
          }
        }

        # 监控特定策略
        subscription {
          tradingEvents(strategyNames: ["MA_CROSS_STRATEGY"]) {
            order { id stockCode status strategyName }
            changes
          }
        }
    """
    wanted_types = set(event_types or [])
    wanted_codes = set(stock_codes or [])
    wanted_strategies = set(strategy_names or [])
    try:
      async for wake_up in runtime_subscription_bridge.subscribe_trading_events():
        order_id = wake_up.get("broker_order_id")
        if order_id is None:
          continue
        order = await OrderService().get_order_by_id(int(order_id))
        if order is None:
          continue
        event_type = self._event_type_for_order(
          order.status,
          str(wake_up.get("message_type") or ""),
        )
        if wanted_types and event_type not in wanted_types:
          continue
        if wanted_codes and order.stock_code not in wanted_codes:
          continue
        if wanted_strategies and order.strategy_name not in wanted_strategies:
          continue
        yield self._convert_order_to_graphql_event(order, event_type)
    except Exception as exc:
      logger.error("交易事件订阅出错: %s", exc)
      raise

  @staticmethod
  def _event_type_for_order(
    status: OrderStatus,
    report_type: str,
  ) -> TradingEventType:
    if status in {
      OrderStatus.CANCELED,
      OrderStatus.PART_CANCEL,
      OrderStatus.PARTSUCC_CANCEL,
    }:
      return TradingEventType.ORDER_CANCELLED
    if status is OrderStatus.JUNK:
      return TradingEventType.ORDER_REJECTED
    if status in {OrderStatus.PART_SUCC, OrderStatus.SUCCEEDED}:
      return TradingEventType.ORDER_FILLED
    if report_type == "execution_report":
      return TradingEventType.ORDER_FILLED
    return TradingEventType.ORDER_CREATED

  @staticmethod
  def _convert_order_to_graphql_event(
    order,
    event_type: TradingEventType,
  ) -> OrderEvent:
    """Convert the durable database order into the public subscription type."""
    return OrderEvent(
      event_type=event_type,
      order=Order(
        id=str(order.id),
        sysid=order.sysid or "",
        stock_code=order.stock_code,
        type=order.type,
        volume=order.volume,
        price_type=order.price_type,
        price=order.price,
        traded_volume=order.traded_volume,
        traded_price=order.traded_price,
        status=order.status,
        status_msg=order.status_msg,
        strategy_name=order.strategy_name,
        time=order.time,
      ),
      time=order.updated_at or order.time,
      changes=str(order.status_msg or ""),
    )

  @strawberry.subscription(description="订阅策略状态流")
  async def system_strategies(
    self,
    info: strawberry.types.Info,
    strategy_ids: Optional[List[int]] = None,
  ) -> AsyncIterator[StrategyStatusInfo]:
    """Poll durable StrategyRun projections and emit only real state changes."""
    principal = principal_from_context(info.context)
    previous: dict[str, tuple[str, Optional[datetime]]] = {}
    try:
      while True:
        statement = select(StrategyRun).where(
          StrategyRun.user_id == principal.user_id
        )
        if strategy_ids:
          statement = statement.where(StrategyRun.strategy_id.in_(strategy_ids))
        async with AsyncSessionLocal() as db:
          result = await db.execute(statement)
          runs = list(result.scalars().all())

        active_ids = {run.id for run in runs}
        previous = {
          run_id: state
          for run_id, state in previous.items()
          if run_id in active_ids
        }
        for run in runs:
          status = getattr(run.status, "value", run.status)
          state = (str(status), run.updated_at)
          if previous.get(run.id) == state:
            continue
          previous[run.id] = state
          metrics = run.metrics or {}
          performance = metrics.get("total_return_pct")
          if performance is not None:
            performance = float(performance)
          yield StrategyStatusInfo(
            strategy_id=run.strategy_id,
            name=run.name,
            status=str(status),
            time=run.updated_at or time_utils.now(),
            message=run.error_message,
            performance=performance,
          )
        await asyncio.sleep(2)
    except Exception as exc:
      logger.error("策略状态订阅出错: %s", exc)
      raise

  @strawberry.subscription(description="订阅系统告警流")
  async def system_alerts(
    self, severity_level: Optional[str] = None
  ) -> AsyncIterator[SystemAlert]:
    """Emit component degradation and recovery alerts from real health state."""
    previous: dict[str, str] = {}
    try:
      while True:
        components = await component_status()
        now = time_utils.now()
        for name in required_components():
          status = str(components[name].get("status") or "unknown").lower()
          old_status = previous.get(name)
          previous[name] = status
          if old_status == status or (old_status is None and status == "ready"):
            continue
          resolved = status == "ready"
          severity = (
            "info"
            if resolved
            else "critical"
            if name in {"database", "engine"}
            else "error"
          )
          if severity_level and severity != severity_level.lower():
            continue
          yield SystemAlert(
            alert_id=f"component:{name}:{status}:{int(now.timestamp())}",
            severity=severity,
            title=f"{name} {'已恢复' if resolved else '状态异常'}",
            message=f"组件 {name} 状态从 {old_status or 'unknown'} 变为 {status}",
            time=now,
            source=name,
            resolved=resolved,
          )
        await asyncio.sleep(5)
    except Exception as exc:
      logger.error("系统告警订阅出错: %s", exc)
      raise

  @strawberry.subscription(description="订阅部署状态流")
  async def deployment_status(
    self, name: str
  ) -> AsyncIterator[DeploymentFlowRun]:
    """
    订阅部署状态变更 (Redis Pub/Sub)

    采用混合策略：
    1. 首次立即返回当前状态
    2. 监听 Redis 频道获取实时更新
    3. 每60秒强制刷新一次作为 fallback

    Args:
        name: 部署名称
    """
    from quantx_infrastructure.database.redis_pubsub import (
      get_deployment_channel,
      redis_pubsub,
    )

    from ..resolvers.prefect import PrefectResolver

    try:
      # 1. 立即返回当前状态
      deployment = await PrefectResolver.get_deployment_by_name(name)
      if deployment:
        yield deployment

      # 2. 订阅 Redis 频道
      channel = get_deployment_channel(name)
      logger.info(f"Subscription started for deployment: {name}")

      async for event in redis_pubsub.subscribe(channel):
        # 收到事件，获取最新状态并推送
        logger.debug(f"Received event: {event}")
        deployment = await PrefectResolver.get_deployment_by_name(name)

        if deployment:
          # 如果 API 查询到的状态为空，但事件中带了状态（比如刚刚手动触发的 Pending）
          # 则将事件中的状态填充进去，防止前端按钮闪烁
          if not deployment.status and isinstance(event, dict) and event.get("status"):
            deployment.status = event["status"]
            logger.debug(f"Supplementing deployment status from event: {deployment.status}")

          yield deployment

    except asyncio.CancelledError:
      logger.info(f"Subscription cancelled for deployment: {name}")
      raise
    except Exception as e:
      logger.error(f"部署状态订阅出错: {e}")
      raise

  @strawberry.subscription(description="订阅 Prefect 流程运行日志")
  async def flow_run_logs(
    self,
    run_id: str,
    include_history: bool = True,
  ) -> AsyncIterator[LogLine]:
    """
    订阅指定流程运行的日志。

    Prefect 日志目前落在 Prefect API/数据库侧，尚未接入 QuantX 自己的事件总线；
    因此这里在后端做轻量增量检查，前端通过 WebSocket 接收新增日志。

    Args:
        run_id: Prefect flow run ID
        include_history: 是否先推送已存在的历史日志
    """
    from ..resolvers.prefect import PrefectResolver

    if not run_id or not run_id.strip():
      raise ValidationError("运行 ID 不能为空")

    flow_run = await PrefectResolver.get_flow_run(run_id)
    if not flow_run:
      raise ValidationError(f"流程运行不存在: {run_id}")

    seen_logs = set()
    initialized = False
    terminal_empty_reads = 0

    try:
      while True:
        flow_run = await PrefectResolver.get_flow_run(run_id)
        raw_logs = flow_run.detailed_logs if flow_run else []
        ordered_logs = sorted(
          raw_logs,
          key=lambda item: _flow_run_log_key(item),
        )
        new_logs = []

        for log in ordered_logs:
          key = _flow_run_log_key(log)
          if key in seen_logs:
            continue

          seen_logs.add(key)
          if initialized or include_history:
            new_logs.append(log)

        initialized = True

        for log in new_logs:
          yield _to_graphql_log_line(log)

        state = flow_run.state if flow_run else None
        if _is_live_flow_run_state(state):
          terminal_empty_reads = 0
        elif new_logs:
          terminal_empty_reads = 0
        else:
          terminal_empty_reads += 1
          if terminal_empty_reads >= 2:
            logger.info("流程运行日志订阅结束: %s, state=%s", run_id, state)
            break

        await asyncio.sleep(FLOW_RUN_LOG_POLL_SECONDS)

    except asyncio.CancelledError:
      logger.info("流程运行日志订阅被取消: %s", run_id)
      raise
    except Exception as e:
      logger.error("流程运行日志订阅出错 run_id=%s: %s", run_id, e)
      raise

  # ==================== 策略运行时订阅 ====================

  @strawberry.subscription(description="订阅策略实例事件流")
  async def strategy_instance_events(
      self,
      instance_id: str,
  ) -> AsyncIterator["StrategyInstanceEvent"]:
    """订阅策略实例状态、日志和心跳事件。"""
    try:
      async for record in runtime_subscription_bridge.stream(
        "strategy-events",
        run_id=instance_id,
      ):
        timestamp = record.get("timestamp") or time_utils.now()
        if isinstance(timestamp, str):
          timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        yield StrategyInstanceEvent(
          instance_id=instance_id,
          event_type=str(record.get("event_type") or "HEARTBEAT"),
          timestamp=timestamp,
          payload=dict(record.get("payload") or {}),
        )
    except asyncio.CancelledError:
      logger.info(f"策略实例事件订阅被取消: {instance_id}")
      raise

  @strawberry.subscription(description="订阅策略运行实例的日志")
  async def strategy_logs(
      self,
      run_id: str,
      levels: Optional[List[str]] = None,
      include_history: bool = True,
  ) -> AsyncIterator["StrategyLogEntry"]:
    """
    订阅策略运行实例的实时日志

    Args:
        run_id: 策略运行实例ID
        levels: 可选的日志级别过滤，如 ["INFO", "ERROR"]
        include_history: 是否在订阅开始时推送历史日志（默认 True）
    """
    level_filter = None
    if levels:
      try:
        level_filter = set(LogLevel[level.upper()] for level in levels)
      except KeyError as e:
        raise ValidationError(f"无效的日志级别: {e}")

    try:
      async for record in runtime_subscription_bridge.stream(
        "strategy-logs",
        run_id=run_id,
        include_history=include_history,
      ):
        graphql_entry = StrategyLogEntry.from_record(run_id, record)
        if level_filter is None or graphql_entry.level in level_filter:
          yield graphql_entry

    except asyncio.CancelledError:
      logger.info(f"策略日志订阅被取消: {run_id}")
      raise

  @strawberry.subscription(description="订阅策略运行实例的 Tick 数据（分时图）")
  async def strategy_ticks(
      self,
      run_id: str,
  ) -> AsyncIterator["StrategyTickData"]:
    """
    订阅策略运行实例正在处理的 Tick 数据

    适用于分时图显示。只推送 Tick 类型的数据。

    Args:
        run_id: 策略运行实例ID
    """
    try:
      async for record in runtime_subscription_bridge.stream(
        "strategy-ticks",
        run_id=run_id,
      ):
        data = runtime_subscription_bridge._model_payload(record)
        event = StrategyMarketDataEvent.from_tick(run_id, Tick(**data))
        yield event.tick

    except asyncio.CancelledError:
      logger.info(f"策略 Tick 订阅被取消: {run_id}")
      raise

  @strawberry.subscription(description="订阅策略运行实例的 K线 数据")
  async def strategy_klines(
      self,
      run_id: str,
      periods: Optional[List[str]] = None,
  ) -> AsyncIterator["StrategyKLineData"]:
    """
    订阅策略运行实例正在处理的 K线 数据

    适用于 K 线图显示。只推送 K线 类型的数据。

    Args:
        run_id: 策略运行实例ID
        periods: 可选的周期过滤，如 ["1m", "5m"]。不传则推送所有周期。
    """
    # 解析周期过滤器
    period_filter = set(periods) if periods else None

    try:
      async for record in runtime_subscription_bridge.stream(
        "strategy-klines",
        run_id=run_id,
      ):
        data = runtime_subscription_bridge._model_payload(record)
        event = StrategyMarketDataEvent.from_kline(run_id, KLine(**data))
        if period_filter is None or event.kline.period in period_filter:
          yield event.kline

    except asyncio.CancelledError:
      logger.info(f"策略 K线 订阅被取消: {run_id}")
      raise
