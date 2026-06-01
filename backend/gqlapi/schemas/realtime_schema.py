import asyncio
import logging
import random
import uuid
from datetime import datetime
from functools import wraps
from typing import AsyncIterator, List, Optional

import strawberry

from core.events import trading_event_manager
from core.events.types import TradingEventType as CoreTradingEventType
from core.realtime_manager import realtime_manager

from core.utils import time_utils
from ..types import (
  KLineData,
  MarketDepth,
  Order,
  OrderEvent,
  RealTimePrice,
  StrategyStatusInfo,
  SystemAlert,
  TickData,
  TradingEventType,
  DeploymentFlowRun,
)

logger = logging.getLogger(__name__)


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

    await realtime_manager.start()

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
            async_iter = realtime_manager.subscribe_price(symbol)
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

    await realtime_manager.start()

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
              async_iter = realtime_manager.subscribe_kline(symbol, period)
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
    await realtime_manager.start()

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
            async_iter = realtime_manager.subscribe_depth(symbol)
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
    await realtime_manager.start()

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
            async_iter = realtime_manager.subscribe_tick(symbol)
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
    try:
      # 转换 GraphQL 枚举到核心事件类型
      core_event_types = None
      if event_types:
        core_event_types = [CoreTradingEventType(et.value) for et in event_types]

      # 订阅事件流
      async for event in trading_event_manager.subscribe(
        event_types=core_event_types,
        stock_codes=stock_codes,
        strategy_names=strategy_names,
      ):
        # 转换核心事件到 GraphQL 事件
        yield self._convert_to_graphql_event(event)

    except Exception as e:
      logger.error(f"交易事件订阅出错: {e}")
      raise

  def _convert_to_graphql_event(self, event) -> OrderEvent:
    """将核心事件转换为 GraphQL 事件 (个人量化软件专用)"""
    from core.events.types import OrderEvent as CoreOrderEvent

    # 只支持 OrderEvent
    if not isinstance(event, CoreOrderEvent):
      raise ValueError(f"只支持订单事件,收到: {type(event)}")

    # 转换订单事件
    order_dict = event.order.to_dict()
    return OrderEvent(
      event_type=TradingEventType(event.event_type.value),
      order=Order(
        id=order_dict["order_id"],
        sysid=order_dict.get("sysid", ""),
        stock_code=order_dict["stock_code"],
        stock_name=order_dict.get("instrument_name", ""),
        type=order_dict["order_type"],
        volume=order_dict["order_volume"],
        price_type=order_dict["price_type"],
        price=order_dict["price"],
        traded_volume=order_dict.get("traded_volume", 0),
        traded_price=order_dict.get("traded_price", 0.0),
        status=order_dict["order_status"],
        status_msg=order_dict.get("status_msg"),
        strategy_name=order_dict.get("strategy_name"),
        order_remark=order_dict.get("remark"),
        time=order_dict["order_time"],
      ),
      time=event.timestamp,
      changes=event.changes,
    )

  @strawberry.subscription(description="订阅策略状态流")
  async def system_strategies(
    self, strategy_ids: Optional[List[int]] = None
  ) -> AsyncIterator[StrategyStatusInfo]:
    """
    订阅策略运行状态，支持策略ID过滤

    Args:
        strategy_ids: 策略ID列表过滤
    """
    try:
      # TODO: 这里应该连接实际的策略管理系统
      # 目前使用模拟数据作为示例

      strategy_data = [
        {"id": 1, "name": "MA交叉策略", "status": "running"},
        {"id": 2, "name": "RSI策略", "status": "paused"},
        {"id": 3, "name": "均值回归策略", "status": "stopped"},
      ]

      while True:
        await asyncio.sleep(5)  # 模拟策略状态更新频率

        for strategy in strategy_data:
          # 根据参数过滤策略
          if strategy_ids and strategy["id"] not in strategy_ids:
            continue

          # 模拟状态变化
          statuses = ["running", "paused", "stopped", "error"]
          current_status = random.choice(statuses)
          performance = (
            random.uniform(-5.0, 10.0) if current_status == "running" else None
          )

          yield StrategyStatusInfo(
            strategy_id=strategy["id"],
            name=strategy["name"],
            status=current_status,
            time=time_utils.now(),
            message=f"策略 {strategy['name']} 状态更新",
            performance=performance,
          )

    except Exception as e:
      logger.error(f"策略状态订阅出错: {e}")
      raise

  @strawberry.subscription(description="订阅系统告警流")
  async def system_alerts(
    self, severity_level: Optional[str] = None
  ) -> AsyncIterator[SystemAlert]:
    """
    订阅系统告警信息，支持告警级别过滤

    Args:
        severity_level: 告警级别过滤,如 "error", "warning", "info", "critical"
    """
    try:
      # TODO: 这里应该连接实际的告警系统
      # 目前使用模拟数据作为示例

      alert_types = [
        {"severity": "error", "title": "数据连接异常", "source": "data_service"},
        {"severity": "warning", "title": "策略性能下降", "source": "strategy_engine"},
        {"severity": "info", "title": "系统维护通知", "source": "system"},
        {"severity": "critical", "title": "风控触发", "source": "risk_engine"},
      ]

      while True:
        await asyncio.sleep(8)  # 模拟告警产生频率

        alert = random.choice(alert_types)

        # 根据参数过滤告警级别
        if severity_level and alert["severity"] != severity_level:
          continue

        yield SystemAlert(
          alert_id=str(uuid.uuid4()),
          severity=alert["severity"],
          title=alert["title"],
          message=f"来自 {alert['source']} 的系统告警",
          time=time_utils.now(),
          source=alert["source"],
          resolved=random.choice([True, False]),
        )

    except Exception as e:
      logger.error(f"系统告警订阅出错: {e}")
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
    from ..resolvers.prefect import PrefectResolver
    from database.redis_pubsub import redis_pubsub, get_deployment_channel

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

  # ==================== 策略运行时订阅 ====================

  @strawberry.subscription(description="订阅策略实例事件流")
  async def strategy_instance_events(
      self,
      instance_id: str,
  ) -> AsyncIterator["StrategyInstanceEvent"]:
    """订阅策略实例状态、日志和心跳事件。"""
    from core.strategy_executor import ExecutionStatus
    from core.strategy_manager import strategy_manager
    from gqlapi.types.strategy_types import StrategyInstanceEvent

    runtime = strategy_manager.executor.runs.get(instance_id)
    if not runtime:
      raise ValidationError(f"策略实例不存在或未加载: {instance_id}")

    yield StrategyInstanceEvent(
      instance_id=instance_id,
      event_type="SNAPSHOT",
      timestamp=time_utils.now(),
      payload={
        "status": runtime.status.value,
        "mode": runtime.mode.value,
        "instruments": list(runtime.instruments or []),
      },
    )

    log_queue = runtime.subscribe_logs(include_history=False)
    try:
      while runtime.status in [
        ExecutionStatus.RUNNING,
        ExecutionStatus.PAUSED,
        ExecutionStatus.STARTING,
        ExecutionStatus.PENDING,
      ]:
        try:
          log_entry = await asyncio.wait_for(log_queue.get(), timeout=10.0)
          yield StrategyInstanceEvent(
            instance_id=instance_id,
            event_type="LOG",
            timestamp=log_entry.timestamp,
            payload={
              "level": getattr(log_entry.level, "value", log_entry.level),
              "message": log_entry.message,
              "source": log_entry.source,
            },
          )
        except asyncio.TimeoutError:
          yield StrategyInstanceEvent(
            instance_id=instance_id,
            event_type="HEARTBEAT",
            timestamp=time_utils.now(),
            payload={"status": runtime.status.value},
          )
    except asyncio.CancelledError:
      logger.info(f"策略实例事件订阅被取消: {instance_id}")
      raise
    finally:
      runtime.unsubscribe_logs(log_queue)

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
    from core.strategy_manager import strategy_manager
    from gqlapi.types.strategy_subscription_types import LogLevel

    # 获取运行时实例
    runtime = strategy_manager.executor.runs.get(run_id)
    if not runtime:
      raise ValidationError(f"策略运行实例不存在: {run_id}")

    # 解析日志级别过滤器
    level_filter = None
    if levels:
      try:
        level_filter = set(LogLevel[level.upper()] for level in levels)
      except KeyError as e:
        raise ValidationError(f"无效的日志级别: {e}")

    # 创建订阅者专属队列
    log_queue = runtime.subscribe_logs(include_history=include_history)
    
    try:
      # 持续监听新日志
      from core.strategy_executor import ExecutionStatus
      while runtime.status in [
        ExecutionStatus.PENDING,
        ExecutionStatus.STARTING,
        ExecutionStatus.RUNNING,
        ExecutionStatus.PAUSED,
      ]:
        try:
          log_entry = await asyncio.wait_for(
            log_queue.get(),
            timeout=5.0
          )
          if level_filter is None or log_entry.level in level_filter:
            yield log_entry
        except asyncio.TimeoutError:
          continue

      logger.info(f"策略日志订阅结束: {run_id}, 状态: {runtime.status}")

    except asyncio.CancelledError:
      logger.info(f"策略日志订阅被取消: {run_id}")
      raise
    finally:
      # 清理：取消订阅
      runtime.unsubscribe_logs(log_queue)

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
    from core.strategy_manager import strategy_manager
    from gqlapi.types.strategy_subscription_types import StrategyMarketDataEvent

    runtime = strategy_manager.executor.runs.get(run_id)
    if not runtime:
      raise ValidationError(f"策略运行实例不存在: {run_id}")

    # 创建订阅者专属队列（只订阅 tick 数据）
    data_queue = runtime.subscribe_data("tick", include_recent=False)

    try:
      from core.strategy_executor import ExecutionStatus
      while runtime.status in [ExecutionStatus.RUNNING, ExecutionStatus.PAUSED, ExecutionStatus.STARTING]:
        try:
          data_type, data = await asyncio.wait_for(
            data_queue.get(),
            timeout=5.0
          )
          
          if data_type == "tick":
            event = StrategyMarketDataEvent.from_tick(run_id, data)
            yield event.tick
            
        except asyncio.TimeoutError:
          continue

      logger.info(f"策略 Tick 订阅结束: {run_id}")

    except asyncio.CancelledError:
      logger.info(f"策略 Tick 订阅被取消: {run_id}")
      raise
    finally:
      runtime.unsubscribe_data(data_queue)

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
    from core.strategy_manager import strategy_manager
    from gqlapi.types.strategy_subscription_types import StrategyMarketDataEvent

    runtime = strategy_manager.executor.runs.get(run_id)
    if not runtime:
      raise ValidationError(f"策略运行实例不存在: {run_id}")

    # 解析周期过滤器
    period_filter = set(periods) if periods else None

    # 创建订阅者专属队列（只订阅 kline 数据）
    data_queue = runtime.subscribe_data("kline", include_recent=False)

    try:
      from core.strategy_executor import ExecutionStatus
      while runtime.status in [ExecutionStatus.RUNNING, ExecutionStatus.PAUSED, ExecutionStatus.STARTING]:
        try:
          data_type, data = await asyncio.wait_for(
            data_queue.get(),
            timeout=5.0
          )
          
          if data_type == "kline":
            event = StrategyMarketDataEvent.from_kline(run_id, data)
            if period_filter is None or event.kline.period in period_filter:
              yield event.kline
            
        except asyncio.TimeoutError:
          continue

      logger.info(f"策略 K线 订阅结束: {run_id}")

    except asyncio.CancelledError:
      logger.info(f"策略 K线 订阅被取消: {run_id}")
      raise
    finally:
      runtime.unsubscribe_data(data_queue)


# 为 strawberry 注解导入类型
from gqlapi.types.strategy_subscription_types import (
    StrategyLogEntry,
    StrategyMarketDataEvent,
    StrategyTickData,
    StrategyKLineData,
)
from gqlapi.types.strategy_types import StrategyInstanceEvent


