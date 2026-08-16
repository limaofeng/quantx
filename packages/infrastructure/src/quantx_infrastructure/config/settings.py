"""
环境变量配置管理模块
使用 pydantic-settings 管理应用配置
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Union
from urllib.parse import urlparse

from pydantic import Field
from pydantic_settings import BaseSettings

env_type = os.getenv("ENV", "development")
WORKSPACE_ROOT = (
  Path(os.environ["QUANTX_ROOT"]).expanduser().resolve()
  if os.environ.get("QUANTX_ROOT")
  else Path(__file__).resolve().parents[5]
)
APP_DIRECTORY = WORKSPACE_ROOT / "apps" / "api"
BASE_ENV_FILE = APP_DIRECTORY / ".env"
ENVIRONMENT_FILE = APP_DIRECTORY / f".env.{env_type}"
OVERRIDE_ENV_FILE = os.getenv("QUANTX_ENV_FILE", "").strip()
SETTINGS_ENV_FILES = [str(BASE_ENV_FILE), str(ENVIRONMENT_FILE)]
if OVERRIDE_ENV_FILE:
  SETTINGS_ENV_FILES.append(str(Path(OVERRIDE_ENV_FILE).expanduser().resolve()))


class Settings(BaseSettings):
  """应用配置类"""

  # 服务器配置
  host: str = Field(default="127.0.0.1", description="服务器监听地址")
  port: int = Field(default=18081, description="服务器端口")
  runtime_profile: str = Field(
    default="web",
    description="运行就绪检查配置：web 或 full",
  )
  public_url: str = Field(
    default="http://127.0.0.1:8080",
    description="浏览器、Agent 与运维探针使用的唯一公开入口",
  )
  debug: bool = Field(default=False, description="调试模式")
  environment: str = Field(
    default="development", validation_alias="ENV", description="运行环境"
  )
  # CORS配置
  cors_origins: Union[List[str], str] = Field(
    default=[
      "http://127.0.0.1:8080",
      "http://localhost:8080",
      "http://localhost:5250",
      "http://localhost:3000",
      "http://localhost:5173",
    ],
    description="允许的CORS源",
  )

  # 关系型数据库配置（PostgreSQL/SQLite）
  database_url: str = Field(
    default="sqlite:///./quantx.db", description="关系型数据库连接URL"
  )
  database_echo: bool = Field(default=False, description="是否输出SQL日志")

  # 时间序列数据库配置（InfluxDB 3.x）
  influxdb_host: str = Field(default="", description="InfluxDB 3.x主机地址")
  influxdb_token: str = Field(default="", description="InfluxDB 3.x访问令牌")
  influxdb_database: str = Field(
    default="quantx_market_data", description="InfluxDB 3.x数据库名"
  )
  # InfluxDB 连接池和性能配置
  influxdb_max_connections: int = Field(default=10, description="InfluxDB最大连接数")
  influxdb_timeout: float = Field(default=30.0, description="InfluxDB连接超时时间(秒)")
  influxdb_pool_acquire_timeout: float = Field(
    default=30.0, description="InfluxDB连接池获取连接等待时间(秒)"
  )
  influxdb_max_retries: int = Field(default=3, description="InfluxDB操作最大重试次数")
  influxdb_retry_delay: float = Field(
    default=1.0, description="InfluxDB重试延迟时间(秒)"
  )
  influxdb_enable_cache: bool = Field(
    default=False, description="是否启用InfluxDB查询缓存"
  )
  influxdb_cache_ttl: int = Field(default=300, description="InfluxDB缓存TTL(秒)")
  influxdb_query_chunk_hours: int = Field(
    default=24,
    description="InfluxDB时间范围查询分片大小(小时)，0表示禁用",
  )
  influxdb_ssl_verify: bool = Field(default=False, description="InfluxDB SSL验证")
  influxdb_ssl_ca_cert: str = Field(default="", description="InfluxDB SSL CA证书路径")

  # 实时数据配置
  realtime_update_interval: int = Field(default=1, description="实时数据更新间隔(秒)")
  kline_cache_size: int = Field(default=100, description="K线数据缓存大小")
  max_subscribers_per_stock: int = Field(default=10, description="每只股票最大订阅者数")
  realtime_generated_kline_save_interval_seconds: float = Field(
    default=10.0,
    description="tick生成1m K线同一分钟快照最小保存间隔(秒)",
  )

  # 交易时间管理配置
  enable_real_trading: bool = Field(
    default=False,
    description="服务端真实交易总开关，默认关闭",
  )
  t_trade_live_enabled: bool = Field(
    default=False,
    description="生产做 T 实盘能力开关，默认关闭",
  )
  real_trading_account_allowlist: Union[List[str], str] = Field(
    default_factory=list,
    description="允许真实交易的账户白名单",
  )
  trading_sessions: Dict[str, List[str]] = Field(
    default={"morning": ["09:30", "11:30"], "afternoon": ["13:00", "15:00"]},
    description="交易时段配置，格式: {'session_name': ['start_time', 'end_time']}",
  )
  pre_market_buffer_minutes: int = Field(
    default=30, description="开盘前提前启动订阅的分钟数"
  )
  post_market_buffer_minutes: int = Field(
    default=15, description="收盘后延迟停止订阅的分钟数"
  )
  subscription_scheduler_enabled: bool = Field(
    default=True, description="是否启用订阅调度器自动管理"
  )
  trading_timezone: str = Field(default="Asia/Shanghai", description="交易时区")
  schedule_check_interval_seconds: int = Field(
    default=60, description="调度检查间隔(秒)"
  )
  trading_days_only: bool = Field(default=True, description="是否仅在交易日启用订阅")
  exclude_holidays: bool = Field(default=True, description="是否排除节假日")

  # 日志配置
  log_level: str = Field(default="INFO", description="日志级别")
  log_file: str = Field(default="logs/quantx.log", description="日志文件路径")
  log_format: str = Field(
    default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    description="日志格式",
  )

  # GraphQL配置
  graphql_debug: bool = Field(default=True, description="GraphQL调试模式")
  graphql_introspection: bool = Field(default=True, description="是否启用GraphQL内省")
  graphql_playground: bool = Field(
    default=True, description="是否启用GraphQL Playground"
  )

  # 安全配置
  secret_key: str = Field(default="change-this-secret-key", description="JWT密钥")
  access_token_expire_minutes: int = Field(
    default=30, description="访问令牌过期时间(分钟)"
  )
  refresh_token_expire_days: int = Field(default=30, description="刷新令牌过期时间(天)")
  algorithm: str = Field(default="HS256", description="JWT算法")
  auth_issuer: str = Field(default="quantx", description="访问令牌签发方")
  auth_audience: str = Field(default="quantx-clients", description="访问令牌受众")
  auth_web_refresh_cookie_name: str = Field(
    default="quantx_refresh",
    pattern=r"^[A-Za-z0-9_-]{1,64}$",
    description="Web 刷新令牌 Cookie 名称",
  )
  auth_web_cookie_secure: Optional[bool] = Field(
    default=None,
    description="Web 刷新令牌 Cookie 是否仅通过 HTTPS 发送；为空时生产环境自动启用",
  )
  auth_web_allowed_origins: Union[List[str], str] = Field(
    default_factory=list,
    description="允许调用 Web 会话接口的浏览器 Origin；为空时复用 CORS_ORIGINS",
  )
  auth_development_auto_login: bool = Field(
    default=False,
    description="仅开发环境允许通过数据库默认用户自动创建 Web 会话",
  )
  auth_development_username: str = Field(
    default="",
    description="开发自动登录使用的数据库用户名；不会把密码发送到前端",
  )
  auth_bootstrap_username: str = Field(
    default="", description="首次启动时创建的本地用户；为空则不创建"
  )
  auth_bootstrap_password: str = Field(
    default="", description="首次启动用户密码；只允许通过环境变量注入"
  )
  auth_bootstrap_display_name: str = Field(
    default="QuantX 用户", description="首次启动用户显示名"
  )
  auth_bootstrap_account_ids: List[str] = Field(
    default_factory=list, description="首次启动用户有权访问的资金账号列表"
  )
  auth_bootstrap_permissions: List[str] = Field(
    default_factory=lambda: [
      "portfolio:read",
      "market:read",
      "strategy:read",
      "orders:read",
      "system-status:read",
      "system-config:write",
      "agent:manage",
      "market:write",
      "operations:write",
      "orders:write",
      "portfolio:write",
      "strategy:write",
      "limit-up:control",
      "liquidation:control",
      "notification:manage",
      "strategy:control",
      "t-trade:control",
      "watchlist:write",
      "assistant:read",
      "assistant:write",
    ],
    description="首次启动用户权限；生产环境不会覆盖既有用户权限",
  )
  auth_login_rate_limit_attempts: int = Field(
    default=5, description="登录失败窗口内最大尝试次数"
  )
  auth_login_rate_limit_window_seconds: int = Field(
    default=300, description="登录失败限流窗口秒数"
  )

  # iOS APNs 投递。默认关闭；私钥只从部署主机上的文件读取，不进入环境值、
  # 数据库、日志或 GraphQL。
  apns_delivery_enabled: bool = Field(
    default=False,
    description="是否启用 iOS APNs outbox 投递，默认关闭",
  )
  apns_team_id: str = Field(default="", description="Apple Developer Team ID")
  apns_key_id: str = Field(default="", description="APNs Auth Key ID")
  apns_topic: str = Field(default="", description="iOS App bundle identifier")
  apns_private_key_file: str = Field(
    default="",
    description="APNs .p8 私钥文件路径；不得填写私钥正文",
  )
  apns_batch_size: int = Field(default=50, ge=1, le=500)
  apns_max_attempts: int = Field(default=5, ge=1, le=20)
  apns_lease_seconds: int = Field(default=120, ge=15, le=900)
  apns_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
  apns_poll_interval_seconds: float = Field(default=10.0, ge=1, le=60)
  apns_delivery_window_seconds: int = Field(
    default=3000,
    ge=60,
    le=3300,
    description="单次 Worker run 的固定持续投递窗口，默认 50 分钟",
  )

  # 监控配置
  metrics_enabled: bool = Field(default=True, description="是否启用监控指标")
  metrics_endpoint: str = Field(default="/metrics", description="监控指标端点")
  health_check_interval: int = Field(default=30, description="健康检查间隔(秒)")

  # 策略配置
  strategy_update_interval: int = Field(default=60, description="策略更新间隔(秒)")
  max_strategy_runs: int = Field(default=10, description="最大策略运行数")
  backtest_replay_window_hours: int = Field(
    default=12, description="回测回放时间窗口(小时)"
  )

  # 性能配置
  max_concurrent_requests: int = Field(default=100, description="最大并发请求数")
  request_timeout: int = Field(default=30, description="请求超时时间(秒)")

  model_config = {
    # Pydantic gives process environment variables priority over dotenv files.
    # Later dotenv files override earlier ones, so the persistent production
    # configuration is: process > QUANTX_ENV_FILE > .env.production > .env.
    "env_file": SETTINGS_ENV_FILES,
    "env_file_encoding": "utf-8",
    "case_sensitive": False,
    "extra": "ignore",
  }

  # 股票数据配置
  mock_data_enabled: bool = Field(default=True, description="是否启用模拟数据")
  price_update_variance: float = Field(default=0.02, description="价格波动幅度")
  volume_range_min: int = Field(default=100000, description="成交量范围最小值")
  volume_range_max: int = Field(default=10000000, description="成交量范围最大值")

  # 缓存配置
  redis_host: str = Field(default="localhost", description="Redis主机地址")
  redis_port: int = Field(default=6379, description="Redis端口")
  redis_db: int = Field(default=0, description="Redis数据库编号")
  redis_password: str = Field(default="", description="Redis密码")
  redis_max_connections: int = Field(default=10, description="Redis最大连接数")
  redis_socket_timeout: Optional[float] = Field(
    default=None, description="Redis套接字超时时间"
  )
  redis_socket_connect_timeout: Optional[float] = Field(
    default=None, description="Redis连接超时时间"
  )
  redis_url: str = Field(default="redis://localhost:6379/0", description="Redis连接URL")
  cache_ttl: int = Field(default=300, description="缓存TTL(秒)")

  # 外部API配置
  tushare_token: str = Field(default="", description="Tushare API Token")

  # LLM/AI 服务配置
  llm_api_key: str = Field(default="", description="LLM API Key (兼容 Gemini 等)")
  llm_api_url: str = Field(
    default="https://generativelanguage.googleapis.com", description="LLM API URL"
  )
  llm_model: str = Field(default="gemini-2.0-flash-exp", description="LLM 模型名称")
  # 向后兼容
  gemini_api_key: str = Field(
    default="", description="Gemini API Key (已废弃，请使用 LLM_API_KEY)"
  )

  # 产品内 AI Assistant 独立运行时
  ai_assistant_enabled: bool = Field(
    default=True,
    description="是否启动产品内 AI Assistant 运行时",
  )
  openai_api_key: str = Field(
    default="",
    description="AI Assistant 使用的 OpenAI API Key，仅允许服务端注入",
  )
  quantx_ai_model: str = Field(
    default="gpt-5.6",
    min_length=1,
    max_length=120,
    description="AI Assistant 使用的 OpenAI 模型",
  )
  ai_assistant_max_concurrent_runs: int = Field(default=2, ge=1, le=16)
  ai_assistant_max_turns: int = Field(default=12, ge=1, le=64)
  ai_assistant_max_tool_calls: int = Field(default=8, ge=1, le=64)
  ai_assistant_run_timeout_seconds: int = Field(default=300, ge=30, le=3600)
  ai_assistant_lease_seconds: int = Field(default=60, ge=15, le=600)
  ai_assistant_tracing_enabled: bool = Field(default=False)

  # Prefect 任务调度配置 - 外部服务模式
  prefect_enabled: bool = Field(default=True, description="是否启用Prefect任务调度")
  prefect_api_url: str = Field(
    default="http://192.168.101.4:30420/api", description="外部Prefect服务器URL"
  )
  prefect_worker_pool: str = Field(
    default="quantx-pool", description="Prefect Worker连接的工作池名称"
  )
  prefect_auto_deploy_flows: bool = Field(default=True, description="是否自动部署Flows")
  conda_env_name: str = Field(
    default_factory=lambda: os.getenv("CONDA_DEFAULT_ENV", ""),
    description="Conda环境名称；为空时使用当前 Python 解释器",
  )
  prefect_home: str = Field(default="./.prefect", description="Prefect工作目录")

  # 调度时间配置 (Cron 表达式)
  daily_sync_cron: str = Field(default="0 6 * * 1-5", description="每日同步Cron表达式")
  realtime_sync_cron: str = Field(
    default="*/5 9-15 * * 1-5", description="实时同步Cron表达式"
  )
  market_sync_cron: str = Field(
    default="0 6,18 * * 1-5", description="市场同步Cron表达式"
  )

  # 任务执行配置
  task_max_retries: int = Field(default=3, description="任务最大重试次数")
  task_retry_delay_seconds: int = Field(default=60, description="任务重试延迟秒数")
  task_cache_enabled: bool = Field(default=True, description="是否启用任务缓存")
  task_cache_expiration_minutes: int = Field(
    default=30, description="任务缓存过期时间(分钟)"
  )

  # 数据同步配置
  stock_data_source: str = Field(default="mock", description="股票数据源")
  enable_database_save: bool = Field(default=True, description="是否保存到数据库")
  enable_sync_reports: bool = Field(default=True, description="是否生成同步报告")
  sync_reports_dir: str = Field(default="logs/sync_reports", description="同步报告目录")

  def __init__(self, **data):
    super().__init__(**data)

    # 处理CORS_ORIGINS字符串转列表
    if isinstance(self.cors_origins, str):
      self.cors_origins = [origin.strip() for origin in self.cors_origins.split(",")]
    if isinstance(self.auth_web_allowed_origins, str):
      self.auth_web_allowed_origins = [
        origin.strip()
        for origin in self.auth_web_allowed_origins.split(",")
        if origin.strip()
      ]
    if isinstance(self.real_trading_account_allowlist, str):
      self.real_trading_account_allowlist = [
        account_id.strip()
        for account_id in self.real_trading_account_allowlist.split(",")
        if account_id.strip()
      ]

    # 强制绕过代理（解决 Windows 注册表回退和 TUN 模式下的 httpx 代理顽疾）
    # 必须设为空字符串 "" 而不是 pop()，以防止 httpx 回退到读取系统注册表
    os.environ["HTTP_PROXY"] = ""
    os.environ["HTTPS_PROXY"] = ""
    os.environ["ALL_PROXY"] = ""
    os.environ["http_proxy"] = ""
    os.environ["https_proxy"] = ""
    os.environ["all_proxy"] = ""

    # 强制让 httpx 认为所有请求都无需代理
    os.environ["NO_PROXY"] = "*"
    os.environ["no_proxy"] = "*"

    # 设置 Prefect 环境变量以禁用遥测和配置服务器连接（在实例化后，使用解析好的字段）
    os.environ["PREFECT_SERVER_ANALYTICS_ENABLED"] = "false"
    os.environ["PREFECT_API_URL"] = self.prefect_api_url
    os.environ["PREFECT_SILENCE_API_URL_MISCONFIGURATION"] = "true"
    os.environ["PREFECT_API_CSRF_ENABLED"] = "false"
    os.environ["PREFECT_HOME"] = os.path.expanduser(self.prefect_home)

  @property
  def is_development(self) -> bool:
    """是否为开发环境"""
    return self.environment.lower() == "development"

  @property
  def is_production(self) -> bool:
    """是否为生产环境"""
    return self.environment.lower() == "production"

  def get_trading_sessions(self) -> Dict[str, List[str]]:
    """获取交易时段配置"""
    return self.trading_sessions

  def is_trading_time_management_enabled(self) -> bool:
    """检查是否启用交易时间管理"""
    return self.subscription_scheduler_enabled and bool(self.trading_sessions)

  def production_validation_errors(self) -> List[str]:
    """Return fail-closed production configuration violations without secrets."""
    if not self.is_production:
      return []

    errors: List[str] = []
    parsed_public_url = urlparse(self.public_url)
    allowed_loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    if (
      parsed_public_url.scheme != "https"
      or parsed_public_url.hostname not in allowed_loopback_hosts
      or parsed_public_url.port != 8080
    ):
      errors.append("PUBLIC_URL must be the loopback HTTPS gateway on port 8080")
    if self.host not in {"127.0.0.1", "localhost"} or self.port != 18081:
      errors.append("production API must listen on 127.0.0.1:18081")
    if not self.database_url.lower().startswith("postgresql+asyncpg://"):
      errors.append("DATABASE_URL must use PostgreSQL with asyncpg")
    if self.debug or self.graphql_debug:
      errors.append("debug and GraphQL debug must be disabled")
    if self.graphql_introspection or self.graphql_playground:
      errors.append("GraphQL introspection and playground must be disabled")
    if self.mock_data_enabled:
      errors.append("mock data must be disabled")
    if (
      len(self.secret_key.strip()) < 48
      or self.secret_key.strip() == "change-this-secret-key"
    ):
      errors.append("SECRET_KEY must be a non-default value of at least 48 chars")
    if self.auth_web_cookie_secure is False:
      errors.append("AUTH_WEB_COOKIE_SECURE must not be disabled")

    public_origin = (
      f"{parsed_public_url.scheme}://{parsed_public_url.netloc}"
      if parsed_public_url.scheme and parsed_public_url.netloc
      else ""
    )
    cors_origins = {str(value).rstrip("/") for value in self.cors_origins}
    auth_origins = {str(value).rstrip("/") for value in self.auth_web_allowed_origins}
    if cors_origins != {public_origin.rstrip("/")}:
      errors.append("CORS_ORIGINS must contain only PUBLIC_URL in production")
    if auth_origins != {public_origin.rstrip("/")}:
      errors.append(
        "AUTH_WEB_ALLOWED_ORIGINS must contain only PUBLIC_URL in production"
      )
    if not self.redis_url.lower().startswith(("redis://", "rediss://")):
      errors.append("REDIS_URL must be configured")
    if not self.influxdb_host or not self.influxdb_database:
      errors.append("InfluxDB host and database must be configured")
    if self.enable_real_trading and not self.real_trading_account_allowlist:
      errors.append(
        "REAL_TRADING_ACCOUNT_ALLOWLIST is required when live trading is enabled"
      )
    if self.apns_delivery_enabled:
      if not all(
        value.strip()
        for value in (self.apns_team_id, self.apns_key_id, self.apns_topic)
      ):
        errors.append("APNs team, key and topic identifiers must be configured")
      private_key_path = Path(self.apns_private_key_file).expanduser()
      if not self.apns_private_key_file or not private_key_path.is_file():
        errors.append("APNS_PRIVATE_KEY_FILE must reference a readable .p8 file")
      if self.apns_lease_seconds < (2 * self.apns_timeout_seconds) + 15:
        errors.append("APNS_LEASE_SECONDS must exceed two provider timeouts")
    return errors

  def validate_production(self) -> None:
    """Refuse unsafe production startup with a secret-free error message."""
    errors = self.production_validation_errors()
    if errors:
      raise RuntimeError("Unsafe production configuration: " + "; ".join(errors))

  def get_log_config(self) -> dict:
    """获取日志配置"""
    # 检查是否支持彩色日志
    try:
      import colorlog  # noqa: F401

      use_color = self.is_development
    except ImportError:
      use_color = False

    config = {
      "version": 1,
      "disable_existing_loggers": False,
      "formatters": {
        "default": {
          "format": "%(asctime)s [%(levelname)-8s] %(name)s - %(message)s",
          "datefmt": "%Y-%m-%d %H:%M:%S",
        },
      },
      "handlers": {
        "default": {
          "formatter": "default",
          "class": "logging.StreamHandler",
          "stream": "ext://sys.stdout",
        },
        "file": {
          "formatter": "default",
          "class": "logging.FileHandler",
          "filename": self.log_file,
          "mode": "a",
          "encoding": "utf-8",
        },
      },
      "loggers": {
        "httpx": {
          "level": "WARNING",
          "propagate": False,
        },
        "httpcore": {
          "level": "WARNING",
          "propagate": False,
        },
        "uvicorn": {
          "level": "INFO",
          "propagate": False,
          "handlers": ["default"],
        },
        "uvicorn.access": {
          "level": "WARNING",
          "propagate": False,
        },
      },
      "root": {
        "level": self.log_level,
        "handlers": ["default", "file"] if self.log_file else ["default"],
      },
    }

    # 如果支持彩色日志，添加彩色格式化器
    if use_color:
      config["formatters"]["colored"] = {
        "()": "colorlog.ColoredFormatter",
        "format": "%(log_color)s%(asctime)s [%(levelname)-8s] %(reset)s%(blue)s%(name)s%(reset)s - %(message)s",
        "datefmt": "%Y-%m-%d %H:%M:%S",
        "log_colors": {
          "DEBUG": "cyan",
          "INFO": "green",
          "WARNING": "yellow",
          "ERROR": "red",
          "CRITICAL": "red,bg_white",
        },
      }
      config["handlers"]["default"]["formatter"] = "colored"

    return config


@lru_cache()
def get_settings() -> Settings:
  """获取应用设置单例"""
  return Settings()


# 全局设置实例
settings = get_settings()


def create_log_directory():
  """创建日志目录"""
  if settings.log_file:
    log_dir = os.path.dirname(settings.log_file)
    if log_dir and not os.path.exists(log_dir):
      os.makedirs(log_dir, exist_ok=True)
