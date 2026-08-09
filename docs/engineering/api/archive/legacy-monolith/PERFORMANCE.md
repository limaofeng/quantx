# ⚡ QuantX 性能优化指南

本文档提供 QuantX 量化交易系统的性能优化策略和最佳实践，帮助提升系统处理能力和响应速度。

## 📋 目录

- [性能指标](#性能指标)
- [系统架构优化](#系统架构优化)
- [数据库性能优化](#数据库性能优化)
- [应用层性能优化](#应用层性能优化)
- [网络和I/O优化](#网络和io优化)
- [缓存策略](#缓存策略)
- [并发处理优化](#并发处理优化)
- [内存管理](#内存管理)
- [监控和分析](#监控和分析)
- [硬件优化建议](#硬件优化建议)

## 📊 性能指标

### 关键性能指标 (KPI)

| 指标 | 目标值 | 监控方法 |
|------|--------|----------|
| API 响应时间 | < 100ms (P95) | Prometheus + Grafana |
| 数据库查询时间 | < 50ms (P95) | pg_stat_statements |
| 内存使用率 | < 80% | htop, free |
| CPU 使用率 | < 70% | htop, top |
| 磁盘 I/O 等待 | < 10% | iostat |
| 网络延迟 | < 5ms | ping, traceroute |
| 策略执行延迟 | < 10ms | 自定义指标 |
| 交易执行延迟 | < 50ms | 交易日志分析 |

### 性能基准测试

```python
# scripts/benchmark.py
import asyncio
import time
import statistics
from concurrent.futures import ThreadPoolExecutor
import aiohttp

class PerformanceBenchmark:
    """性能基准测试工具"""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.results = []

    async def test_api_latency(self, endpoint: str, concurrent_requests: int = 100):
        """测试 API 延迟"""
        async with aiohttp.ClientSession() as session:
            tasks = []
            for _ in range(concurrent_requests):
                task = self._single_request(session, f"{self.base_url}{endpoint}")
                tasks.append(task)

            start_time = time.time()
            results = await asyncio.gather(*tasks, return_exceptions=True)
            total_time = time.time() - start_time

            success_times = [r for r in results if isinstance(r, float)]
            success_rate = len(success_times) / len(results) * 100

            return {
                "endpoint": endpoint,
                "concurrent_requests": concurrent_requests,
                "total_time": total_time,
                "success_rate": success_rate,
                "avg_latency": statistics.mean(success_times) if success_times else 0,
                "p95_latency": statistics.quantiles(success_times, n=20)[18] if len(success_times) > 20 else 0,
                "throughput": len(success_times) / total_time
            }

    async def _single_request(self, session: aiohttp.ClientSession, url: str) -> float:
        """单次请求测试"""
        start_time = time.time()
        try:
            async with session.get(url) as response:
                await response.text()
                return time.time() - start_time
        except Exception as e:
            return e

    def test_database_performance(self):
        """数据库性能测试"""
        import psycopg2
        import time

        conn = psycopg2.connect("postgresql://quantx:password@localhost/quantx")
        cursor = conn.cursor()

        # 测试简单查询
        start_time = time.time()
        cursor.execute("SELECT count(*) FROM strategies;")
        simple_query_time = time.time() - start_time

        # 测试复杂查询
        start_time = time.time()
        cursor.execute("""
            SELECT s.name, count(sr.id) as runs
            FROM strategies s
            LEFT JOIN strategy_runs sr ON s.id = sr.strategy_id
            GROUP BY s.id, s.name
            ORDER BY runs DESC;
        """)
        complex_query_time = time.time() - start_time

        conn.close()

        return {
            "simple_query_time": simple_query_time,
            "complex_query_time": complex_query_time
        }

# 使用示例
async def run_benchmark():
    benchmark = PerformanceBenchmark()

    # API 性能测试
    health_test = await benchmark.test_api_latency("/health", 100)
    api_test = await benchmark.test_api_latency("/graphql", 50)

    print("API 性能测试结果:")
    print(f"健康检查: {health_test['avg_latency']:.3f}s 平均延迟")
    print(f"GraphQL: {api_test['avg_latency']:.3f}s 平均延迟")

    # 数据库性能测试
    db_test = benchmark.test_database_performance()
    print(f"数据库查询: {db_test['simple_query_time']:.3f}s")

if __name__ == "__main__":
    asyncio.run(run_benchmark())
```

## 🏗️ 系统架构优化

### 微服务架构拆分

```python
# 服务拆分建议
services = {
    "market-data-service": {
        "purpose": "市场数据获取和处理",
        "scaling": "高并发读取",
        "resources": "CPU密集型"
    },
    "strategy-engine": {
        "purpose": "策略计算和意图生成",
        "scaling": "计算密集型",
        "resources": "CPU + 内存密集型"
    },
    "trading-service": {
        "purpose": "交易执行和订单管理",
        "scaling": "低延迟要求",
        "resources": "网络I/O密集型"
    },
    "data-storage": {
        "purpose": "数据存储和查询",
        "scaling": "存储密集型",
        "resources": "磁盘I/O密集型"
    }
}
```

### 负载均衡配置

```nginx
# nginx.conf 高性能配置
upstream quantx_api {
    least_conn;  # 最少连接数算法
    server 127.0.0.1:8000 weight=3 max_fails=3 fail_timeout=30s;
    server 127.0.0.1:8001 weight=3 max_fails=3 fail_timeout=30s;
    server 127.0.0.1:8002 weight=2 max_fails=3 fail_timeout=30s;
    keepalive 32;  # 保持连接池
}

server {
    listen 80;

    # 连接优化
    keepalive_timeout 65;
    keepalive_requests 1000;

    # 压缩配置
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types text/plain application/json application/javascript;

    # 缓存配置
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg)$ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    location / {
        proxy_pass http://quantx_api;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache_bypass $http_upgrade;
    }
}
```

### 分布式部署

```yaml
# kubernetes/deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: quantx-api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: quantx-api
  template:
    metadata:
      labels:
        app: quantx-api
    spec:
      containers:
      - name: quantx-api
        image: quantx/backend:latest
        resources:
          requests:
            memory: "512Mi"
            cpu: "500m"
          limits:
            memory: "1Gi"
            cpu: "1000m"
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: quantx-secrets
              key: database-url
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 30
          periodSeconds: 10
        readinessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 5
```

## 🗄️ 数据库性能优化

### PostgreSQL 配置优化

```sql
-- postgresql.conf 性能优化配置
-- 内存配置
shared_buffers = 2GB                    -- 系统内存的 25%
effective_cache_size = 6GB              -- 系统内存的 75%
work_mem = 256MB                        -- 单个查询可用内存
maintenance_work_mem = 512MB             -- 维护操作内存

-- 检查点配置
checkpoint_completion_target = 0.8      -- 检查点完成目标
wal_buffers = 64MB                      -- WAL 缓冲区
max_wal_size = 4GB                      -- 最大 WAL 大小

-- 连接配置
max_connections = 200                   -- 最大连接数
shared_preload_libraries = 'pg_stat_statements'

-- 查询优化
random_page_cost = 1.1                  -- SSD 随机访问成本
effective_io_concurrency = 200          -- SSD 并发 I/O

-- 统计信息
default_statistics_target = 500         -- 统计信息精度
```

### 索引优化策略

```sql
-- 1. 为常用查询创建索引
CREATE INDEX CONCURRENTLY idx_orders_symbol_time
ON orders (symbol, create_time DESC);

CREATE INDEX CONCURRENTLY idx_klines_symbol_interval_time
ON klines (symbol, interval, timestamp DESC);

CREATE INDEX CONCURRENTLY idx_strategy_runs_status
ON strategy_runs (status) WHERE status IN ('running', 'pending');

-- 2. 复合索引优化
CREATE INDEX CONCURRENTLY idx_market_data_symbol_timestamp
ON market_data (symbol, timestamp DESC)
INCLUDE (price, volume);

-- 3. 部分索引
CREATE INDEX CONCURRENTLY idx_orders_active
ON orders (symbol, create_time)
WHERE status IN ('pending', 'partial');

-- 4. 表达式索引
CREATE INDEX CONCURRENTLY idx_orders_date
ON orders (DATE(create_time));

-- 5. 分析索引使用情况
SELECT schemaname, tablename, indexname, idx_scan, idx_tup_read, idx_tup_fetch
FROM pg_stat_user_indexes
ORDER BY idx_scan DESC;

-- 6. 查找未使用的索引
SELECT schemaname, tablename, indexname, idx_scan
FROM pg_stat_user_indexes
WHERE idx_scan = 0 AND indexname NOT LIKE '%_pkey';
```

### 查询优化

```python
# database/optimized_queries.py
from sqlalchemy import text
from typing import List, Dict, Any

class OptimizedQueries:
    """优化的数据库查询"""

    @staticmethod
    async def get_market_data_batch(
        session,
        symbols: List[str],
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """批量获取市场数据"""
        # 使用参数化查询和批量操作
        query = text("""
            SELECT symbol, price, volume, timestamp
            FROM market_data
            WHERE symbol = ANY(:symbols)
            AND timestamp >= NOW() - INTERVAL '1 day'
            ORDER BY timestamp DESC
            LIMIT :limit
        """)

        result = await session.execute(query, {
            "symbols": symbols,
            "limit": limit
        })
        return [dict(row) for row in result.fetchall()]

    @staticmethod
    async def get_strategy_performance_summary(
        session,
        strategy_ids: List[int]
    ) -> List[Dict[str, Any]]:
        """策略绩效汇总查询"""
        # 使用窗口函数优化聚合查询
        query = text("""
            WITH strategy_stats AS (
                SELECT
                    strategy_id,
                    COUNT(*) as total_runs,
                    AVG(profit_ratio) as avg_profit,
                    STDDEV(profit_ratio) as volatility,
                    MAX(profit_ratio) as max_profit,
                    MIN(profit_ratio) as max_loss,
                    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY profit_ratio) as median_profit
                FROM strategy_runs
                WHERE strategy_id = ANY(:strategy_ids)
                AND status = 'completed'
                AND end_time >= NOW() - INTERVAL '30 days'
                GROUP BY strategy_id
            )
            SELECT
                s.id,
                s.name,
                COALESCE(ss.total_runs, 0) as total_runs,
                COALESCE(ss.avg_profit, 0) as avg_profit,
                COALESCE(ss.volatility, 0) as volatility,
                COALESCE(ss.max_profit, 0) as max_profit,
                COALESCE(ss.max_loss, 0) as max_loss,
                COALESCE(ss.median_profit, 0) as median_profit
            FROM strategies s
            LEFT JOIN strategy_stats ss ON s.id = ss.strategy_id
            WHERE s.id = ANY(:strategy_ids)
        """)

        result = await session.execute(query, {"strategy_ids": strategy_ids})
        return [dict(row) for row in result.fetchall()]

# 连接池优化
from sqlalchemy.pool import QueuePool

engine = create_async_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=20,           # 连接池大小
    max_overflow=30,        # 溢出连接数
    pool_timeout=30,        # 连接超时
    pool_recycle=3600,      # 连接回收时间
    pool_pre_ping=True,     # 连接预检
    echo=False              # 生产环境关闭 SQL 日志
)
```

### 分区表策略

```sql
-- 按时间分区的市场数据表
CREATE TABLE market_data (
    id BIGSERIAL,
    symbol VARCHAR(20) NOT NULL,
    price DECIMAL(10,2) NOT NULL,
    volume BIGINT NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);

-- 创建月度分区
CREATE TABLE market_data_2025_01 PARTITION OF market_data
FOR VALUES FROM ('2025-01-01') TO ('2025-02-01');

CREATE TABLE market_data_2025_02 PARTITION OF market_data
FOR VALUES FROM ('2025-02-01') TO ('2025-03-01');

-- 自动分区管理
CREATE OR REPLACE FUNCTION create_monthly_partition(table_name text, start_date date)
RETURNS void AS $$
DECLARE
    partition_name text;
    end_date date;
BEGIN
    partition_name := table_name || '_' || to_char(start_date, 'YYYY_MM');
    end_date := start_date + interval '1 month';

    EXECUTE format('CREATE TABLE %I PARTITION OF %I
                    FOR VALUES FROM (%L) TO (%L)',
                   partition_name, table_name, start_date, end_date);

    EXECUTE format('CREATE INDEX ON %I (symbol, timestamp)', partition_name);
END;
$$ LANGUAGE plpgsql;
```

## 💻 应用层性能优化

### 异步处理优化

```python
# services/async_optimization.py
import asyncio
import aiohttp
from asyncio import Semaphore
from typing import List, Dict, Any

class AsyncOptimizer:
    """异步处理优化器"""

    def __init__(self, max_concurrent: int = 100):
        self.semaphore = Semaphore(max_concurrent)
        self.session = None

    async def __aenter__(self):
        connector = aiohttp.TCPConnector(
            limit=100,                  # 总连接池大小
            limit_per_host=30,          # 每个主机的连接数
            ttl_dns_cache=300,          # DNS 缓存时间
            use_dns_cache=True,         # 启用 DNS 缓存
            keepalive_timeout=30,       # 保持连接时间
            enable_cleanup_closed=True  # 自动清理关闭的连接
        )

        timeout = aiohttp.ClientTimeout(
            total=30,       # 总超时时间
            connect=5,      # 连接超时
            sock_read=10    # 读取超时
        )

        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def fetch_market_data_batch(
        self,
        symbols: List[str]
    ) -> Dict[str, Any]:
        """批量获取市场数据"""
        async def fetch_single(symbol: str):
            async with self.semaphore:
                try:
                    url = f"https://api.example.com/market/{symbol}"
                    async with self.session.get(url) as response:
                        return symbol, await response.json()
                except Exception as e:
                    return symbol, {"error": str(e)}

        tasks = [fetch_single(symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        return {symbol: data for symbol, data in results if not isinstance(data, Exception)}

# 使用连接池
async def optimized_data_fetching():
    async with AsyncOptimizer(max_concurrent=50) as optimizer:
        symbols = ["AAPL", "GOOGL", "MSFT", "TSLA"]
        data = await optimizer.fetch_market_data_batch(symbols)
        return data
```

### 缓存装饰器

```python
# utils/cache_decorators.py
import asyncio
import pickle
import hashlib
from functools import wraps
from typing import Any, Callable, Optional
import redis.asyncio as redis

class AsyncCache:
    """异步缓存装饰器"""

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_pool = redis.ConnectionPool.from_url(redis_url)

    def cache(
        self,
        ttl: int = 300,
        key_prefix: str = "",
        serialize: str = "pickle"
    ):
        """缓存装饰器"""
        def decorator(func: Callable) -> Callable:
            @wraps(func)
            async def wrapper(*args, **kwargs) -> Any:
                # 生成缓存键
                cache_key = self._generate_cache_key(func, args, kwargs, key_prefix)

                # 尝试从缓存获取
                redis_client = redis.Redis(connection_pool=self.redis_pool)
                try:
                    cached_data = await redis_client.get(cache_key)
                    if cached_data:
                        if serialize == "pickle":
                            return pickle.loads(cached_data)
                        else:
                            return json.loads(cached_data)
                except Exception:
                    pass  # 缓存失败不影响主逻辑

                # 执行原函数
                result = await func(*args, **kwargs)

                # 存储到缓存
                try:
                    if serialize == "pickle":
                        serialized_data = pickle.dumps(result)
                    else:
                        serialized_data = json.dumps(result)

                    await redis_client.setex(cache_key, ttl, serialized_data)
                except Exception:
                    pass  # 缓存失败不影响主逻辑
                finally:
                    await redis_client.close()

                return result
            return wrapper
        return decorator

    def _generate_cache_key(
        self,
        func: Callable,
        args: tuple,
        kwargs: dict,
        prefix: str
    ) -> str:
        """生成缓存键"""
        func_name = f"{func.__module__}.{func.__name__}"
        args_str = str(args) + str(sorted(kwargs.items()))
        args_hash = hashlib.md5(args_str.encode()).hexdigest()
        return f"{prefix}:{func_name}:{args_hash}"

# 使用示例
cache = AsyncCache()

@cache.cache(ttl=60, key_prefix="market_data")
async def get_market_data(symbol: str) -> dict:
    # 模拟耗时操作
    await asyncio.sleep(1)
    return {"symbol": symbol, "price": 100.0}
```

### 批量处理优化

```python
# utils/batch_processor.py
import asyncio
from typing import List, Callable, Any, TypeVar
from collections import defaultdict

T = TypeVar('T')
R = TypeVar('R')

class BatchProcessor:
    """批量处理器"""

    def __init__(self, batch_size: int = 100, max_wait_time: float = 1.0):
        self.batch_size = batch_size
        self.max_wait_time = max_wait_time
        self.pending_batches = defaultdict(list)
        self.batch_futures = defaultdict(list)

    async def add_to_batch(
        self,
        batch_key: str,
        item: T,
        processor: Callable[[List[T]], List[R]]
    ) -> R:
        """添加项目到批次"""
        future = asyncio.Future()

        self.pending_batches[batch_key].append(item)
        self.batch_futures[batch_key].append(future)

        # 检查是否达到批次大小或超时
        if len(self.pending_batches[batch_key]) >= self.batch_size:
            await self._process_batch(batch_key, processor)
        else:
            # 设置超时处理
            asyncio.create_task(self._process_batch_after_timeout(batch_key, processor))

        return await future

    async def _process_batch(self, batch_key: str, processor: Callable):
        """处理批次"""
        if not self.pending_batches[batch_key]:
            return

        items = self.pending_batches[batch_key]
        futures = self.batch_futures[batch_key]

        # 清空当前批次
        self.pending_batches[batch_key] = []
        self.batch_futures[batch_key] = []

        try:
            results = await processor(items)
            for future, result in zip(futures, results):
                if not future.done():
                    future.set_result(result)
        except Exception as e:
            for future in futures:
                if not future.done():
                    future.set_exception(e)

    async def _process_batch_after_timeout(self, batch_key: str, processor: Callable):
        """超时后处理批次"""
        await asyncio.sleep(self.max_wait_time)
        await self._process_batch(batch_key, processor)

# 使用示例
batch_processor = BatchProcessor(batch_size=50, max_wait_time=0.5)

async def get_stock_prices_batch(symbols: List[str]) -> List[float]:
    """批量获取股票价格"""
    # 模拟批量API调用
    await asyncio.sleep(0.1)
    return [100.0 + hash(symbol) % 50 for symbol in symbols]

async def get_stock_price(symbol: str) -> float:
    """单个股票价格获取（会被自动批量处理）"""
    return await batch_processor.add_to_batch(
        "stock_prices",
        symbol,
        get_stock_prices_batch
    )
```

## 🌐 网络和I/O优化

### HTTP客户端优化

```python
# utils/http_client.py
import aiohttp
import asyncio
from typing import Optional, Dict, Any

class OptimizedHTTPClient:
    """优化的HTTP客户端"""

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None

    async def create_session(self):
        """创建优化的会话"""
        connector = aiohttp.TCPConnector(
            limit=100,                    # 总连接数限制
            limit_per_host=30,            # 每个主机连接数限制
            ttl_dns_cache=300,            # DNS缓存时间（秒）
            use_dns_cache=True,           # 启用DNS缓存
            keepalive_timeout=60,         # Keep-alive超时时间
            enable_cleanup_closed=True,   # 自动清理关闭的连接
            force_close=False,            # 不强制关闭连接
            ssl=False                     # 根据需要配置SSL
        )

        timeout = aiohttp.ClientTimeout(
            total=30,           # 总超时时间
            connect=10,         # 连接超时时间
            sock_read=20        # 读取超时时间
        )

        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={
                'User-Agent': 'QuantX/1.0',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive'
            }
        )

    async def close_session(self):
        """关闭会话"""
        if self.session:
            await self.session.close()

    async def get_with_retry(
        self,
        url: str,
        max_retries: int = 3,
        backoff_factor: float = 0.5,
        **kwargs
    ) -> Dict[str, Any]:
        """带重试的GET请求"""
        for attempt in range(max_retries + 1):
            try:
                async with self.session.get(url, **kwargs) as response:
                    response.raise_for_status()
                    return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if attempt == max_retries:
                    raise e

                # 指数退避
                wait_time = backoff_factor * (2 ** attempt)
                await asyncio.sleep(wait_time)

# 全局HTTP客户端实例
http_client = OptimizedHTTPClient()

# 应用启动时初始化
async def startup():
    await http_client.create_session()

# 应用关闭时清理
async def shutdown():
    await http_client.close_session()
```

### 文件I/O优化

```python
# utils/file_io.py
import aiofiles
import asyncio
import json
from pathlib import Path
from typing import List, Dict, Any

class OptimizedFileIO:
    """优化的文件I/O操作"""

    @staticmethod
    async def read_json_files_batch(file_paths: List[Path]) -> List[Dict[str, Any]]:
        """批量读取JSON文件"""
        async def read_single_file(file_path: Path) -> Dict[str, Any]:
            try:
                async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    return json.loads(content)
            except Exception as e:
                return {"error": str(e), "file": str(file_path)}

        tasks = [read_single_file(path) for path in file_paths]
        return await asyncio.gather(*tasks)

    @staticmethod
    async def write_json_files_batch(data_files: List[tuple]) -> None:
        """批量写入JSON文件"""
        async def write_single_file(file_path: Path, data: Dict[str, Any]) -> None:
            try:
                async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(data, ensure_ascii=False, indent=2))
            except Exception as e:
                print(f"写入文件失败 {file_path}: {e}")

        tasks = [write_single_file(path, data) for path, data in data_files]
        await asyncio.gather(*tasks)

    @staticmethod
    async def append_to_log_file(file_path: Path, log_entry: str) -> None:
        """异步追加日志"""
        async with aiofiles.open(file_path, 'a', encoding='utf-8') as f:
            timestamp = asyncio.get_event_loop().time()
            await f.write(f"[{timestamp}] {log_entry}\n")
```

## 🔄 缓存策略

### 多级缓存架构

```python
# cache/multi_level_cache.py
import asyncio
import pickle
from abc import ABC, abstractmethod
from typing import Any, Optional, Dict
import redis.asyncio as redis
from cachetools import TTLCache

class CacheLevel(ABC):
    """缓存级别抽象类"""

    @abstractmethod
    async def get(self, key: str) -> Optional[Any]:
        pass

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        pass

class MemoryCache(CacheLevel):
    """内存缓存（L1）"""

    def __init__(self, maxsize: int = 1000):
        self.cache = TTLCache(maxsize=maxsize, ttl=300)

    async def get(self, key: str) -> Optional[Any]:
        return self.cache.get(key)

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        self.cache[key] = value

    async def delete(self, key: str) -> None:
        self.cache.pop(key, None)

class RedisCache(CacheLevel):
    """Redis缓存（L2）"""

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_pool = redis.ConnectionPool.from_url(redis_url)

    async def get(self, key: str) -> Optional[Any]:
        redis_client = redis.Redis(connection_pool=self.redis_pool)
        try:
            data = await redis_client.get(key)
            if data:
                return pickle.loads(data)
        except Exception:
            pass
        finally:
            await redis_client.close()
        return None

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        redis_client = redis.Redis(connection_pool=self.redis_pool)
        try:
            serialized_data = pickle.dumps(value)
            await redis_client.setex(key, ttl, serialized_data)
        except Exception:
            pass
        finally:
            await redis_client.close()

    async def delete(self, key: str) -> None:
        redis_client = redis.Redis(connection_pool=self.redis_pool)
        try:
            await redis_client.delete(key)
        except Exception:
            pass
        finally:
            await redis_client.close()

class MultiLevelCache:
    """多级缓存管理器"""

    def __init__(self):
        self.l1_cache = MemoryCache(maxsize=1000)  # 内存缓存
        self.l2_cache = RedisCache()               # Redis缓存

    async def get(self, key: str) -> Optional[Any]:
        """从多级缓存获取数据"""
        # 首先尝试L1缓存
        value = await self.l1_cache.get(key)
        if value is not None:
            return value

        # 尝试L2缓存
        value = await self.l2_cache.get(key)
        if value is not None:
            # 回填L1缓存
            await self.l1_cache.set(key, value)
            return value

        return None

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        """设置多级缓存"""
        await asyncio.gather(
            self.l1_cache.set(key, value, ttl),
            self.l2_cache.set(key, value, ttl)
        )

    async def delete(self, key: str) -> None:
        """删除多级缓存"""
        await asyncio.gather(
            self.l1_cache.delete(key),
            self.l2_cache.delete(key)
        )

# 全局缓存实例
cache = MultiLevelCache()

# 缓存装饰器
def cached(ttl: int = 300, key_func=None):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                cache_key = f"{func.__name__}:{hash(str(args) + str(kwargs))}"

            # 尝试从缓存获取
            result = await cache.get(cache_key)
            if result is not None:
                return result

            # 执行函数并缓存结果
            result = await func(*args, **kwargs)
            await cache.set(cache_key, result, ttl)

            return result
        return wrapper
    return decorator
```

### 缓存预热策略

```python
# cache/cache_warming.py
import asyncio
from typing import List
from services.market_data_service import MarketDataService

class CacheWarmer:
    """缓存预热器"""

    def __init__(self, market_service: MarketDataService):
        self.market_service = market_service

    async def warm_market_data_cache(self, symbols: List[str]) -> None:
        """预热市场数据缓存"""
        tasks = []
        for symbol in symbols:
            task = self.market_service.get_latest_market_data(symbol)
            tasks.append(task)

        # 并发预热
        await asyncio.gather(*tasks, return_exceptions=True)

    async def warm_strategy_cache(self) -> None:
        """预热策略缓存"""
        # 预加载活跃策略
        from services.strategy_service import StrategyService
        strategy_service = StrategyService()

        await strategy_service.get_active_strategies()

    async def schedule_cache_warming(self) -> None:
        """定期缓存预热"""
        while True:
            try:
                # 每5分钟预热一次
                popular_symbols = ["000001.SZ", "000002.SZ", "600000.SH"]
                await self.warm_market_data_cache(popular_symbols)
                await self.warm_strategy_cache()
            except Exception as e:
                print(f"缓存预热失败: {e}")

            await asyncio.sleep(300)  # 5分钟
```

## 🚀 并发处理优化

### 并发量控制

```python
# utils/concurrency.py
import asyncio
from asyncio import Semaphore
from typing import List, Callable, Any

class ConcurrencyController:
    """并发控制器"""

    def __init__(self, max_concurrent: int = 10):
        self.semaphore = Semaphore(max_concurrent)

    async def run_with_limit(self, coro):
        """限制并发执行"""
        async with self.semaphore:
            return await coro

    async def map_concurrent(
        self,
        func: Callable,
        items: List[Any],
        max_concurrent: int = None
    ) -> List[Any]:
        """并发映射函数"""
        if max_concurrent:
            semaphore = Semaphore(max_concurrent)
        else:
            semaphore = self.semaphore

        async def limited_func(item):
            async with semaphore:
                return await func(item)

        tasks = [limited_func(item) for item in items]
        return await asyncio.gather(*tasks)

# 使用示例
concurrency_controller = ConcurrencyController(max_concurrent=20)

async def process_symbols(symbols: List[str]) -> List[dict]:
    """并发处理股票符号"""
    async def process_single_symbol(symbol: str) -> dict:
        # 模拟耗时操作
        await asyncio.sleep(0.1)
        return {"symbol": symbol, "processed": True}

    return await concurrency_controller.map_concurrent(
        process_single_symbol,
        symbols,
        max_concurrent=10
    )
```

### 线程池优化

```python
# utils/thread_pool.py
import asyncio
import concurrent.futures
from typing import Callable, Any, List

class OptimizedThreadPool:
    """优化的线程池"""

    def __init__(self, max_workers: int = None):
        if max_workers is None:
            max_workers = min(32, (os.cpu_count() or 1) + 4)

        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="quantx_"
        )

    async def run_in_thread(self, func: Callable, *args, **kwargs) -> Any:
        """在线程池中运行函数"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, func, *args, **kwargs)

    async def map_in_threads(
        self,
        func: Callable,
        items: List[Any]
    ) -> List[Any]:
        """在线程池中映射函数"""
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(self.executor, func, item)
            for item in items
        ]
        return await asyncio.gather(*tasks)

    def shutdown(self, wait: bool = True) -> None:
        """关闭线程池"""
        self.executor.shutdown(wait=wait)

# 全局线程池
thread_pool = OptimizedThreadPool()

# CPU密集型任务示例
def calculate_complex_indicator(prices: List[float]) -> float:
    """CPU密集型指标计算"""
    import numpy as np
    # 复杂的数学计算
    return np.mean(prices) * np.std(prices)

async def process_indicators_parallel(data: List[List[float]]) -> List[float]:
    """并行处理指标"""
    return await thread_pool.map_in_threads(
        calculate_complex_indicator,
        data
    )
```

## 🧠 内存管理

### 内存监控

```python
# monitoring/memory_monitor.py
import psutil
import asyncio
import gc
import tracemalloc
from typing import Dict, Any

class MemoryMonitor:
    """内存监控器"""

    def __init__(self):
        self.start_trace()

    def start_trace(self):
        """开始内存跟踪"""
        tracemalloc.start()

    def get_memory_stats(self) -> Dict[str, Any]:
        """获取内存统计信息"""
        process = psutil.Process()
        memory_info = process.memory_info()
        memory_percent = process.memory_percent()

        # 获取tracemalloc统计
        snapshot = tracemalloc.take_snapshot()
        top_stats = snapshot.statistics('lineno')

        return {
            "rss": memory_info.rss / 1024 / 1024,  # MB
            "vms": memory_info.vms / 1024 / 1024,  # MB
            "percent": memory_percent,
            "available": psutil.virtual_memory().available / 1024 / 1024,  # MB
            "top_memory_usage": [
                {
                    "file": stat.traceback.format()[0],
                    "size_mb": stat.size / 1024 / 1024
                }
                for stat in top_stats[:5]
            ]
        }

    def force_garbage_collection(self) -> Dict[str, int]:
        """强制垃圾回收"""
        collected = {}
        for generation in range(3):
            collected[f"gen_{generation}"] = gc.collect(generation)

        return collected

    async def monitor_memory_usage(self, threshold_mb: float = 1000.0):
        """监控内存使用"""
        while True:
            stats = self.get_memory_stats()

            if stats["rss"] > threshold_mb:
                print(f"⚠️ 内存使用过高: {stats['rss']:.2f}MB")

                # 强制垃圾回收
                collected = self.force_garbage_collection()
                print(f"垃圾回收: {collected}")

                # 记录内存热点
                print("内存热点:")
                for usage in stats["top_memory_usage"]:
                    print(f"  {usage['file']}: {usage['size_mb']:.2f}MB")

            await asyncio.sleep(30)  # 每30秒检查一次

# 全局内存监控器
memory_monitor = MemoryMonitor()
```

### 对象池

```python
# utils/object_pool.py
import asyncio
from typing import TypeVar, Generic, Callable, Optional
from collections import deque

T = TypeVar('T')

class ObjectPool(Generic[T]):
    """对象池"""

    def __init__(
        self,
        factory: Callable[[], T],
        reset_func: Optional[Callable[[T], None]] = None,
        max_size: int = 100
    ):
        self.factory = factory
        self.reset_func = reset_func
        self.max_size = max_size
        self.pool = deque()
        self.lock = asyncio.Lock()

    async def acquire(self) -> T:
        """获取对象"""
        async with self.lock:
            if self.pool:
                return self.pool.popleft()
            else:
                return self.factory()

    async def release(self, obj: T) -> None:
        """释放对象"""
        async with self.lock:
            if len(self.pool) < self.max_size:
                if self.reset_func:
                    self.reset_func(obj)
                self.pool.append(obj)

    async def __aenter__(self) -> T:
        return await self.acquire()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # 注意：这里需要存储对象引用
        pass

# 使用示例
class DataProcessor:
    def __init__(self):
        self.data = []

    def reset(self):
        self.data.clear()

def create_processor() -> DataProcessor:
    return DataProcessor()

def reset_processor(processor: DataProcessor) -> None:
    processor.reset()

# 创建对象池
processor_pool = ObjectPool(
    factory=create_processor,
    reset_func=reset_processor,
    max_size=50
)

async def process_data_with_pool(data):
    """使用对象池处理数据"""
    processor = await processor_pool.acquire()
    try:
        processor.data = data
        # 处理逻辑
        result = len(processor.data)
        return result
    finally:
        await processor_pool.release(processor)
```

## 📊 监控和分析

### 性能指标收集

```python
# monitoring/metrics_collector.py
import time
import asyncio
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from collections import defaultdict

@dataclass
class PerformanceMetrics:
    """性能指标"""
    total_requests: int = 0
    total_response_time: float = 0.0
    error_count: int = 0
    min_response_time: float = float('inf')
    max_response_time: float = 0.0
    response_times: list = field(default_factory=list)

class MetricsCollector:
    """指标收集器"""

    def __init__(self):
        self.metrics = defaultdict(PerformanceMetrics)
        self.start_time = time.time()

    def record_request(
        self,
        endpoint: str,
        response_time: float,
        success: bool = True
    ) -> None:
        """记录请求指标"""
        metric = self.metrics[endpoint]

        metric.total_requests += 1
        metric.total_response_time += response_time

        if not success:
            metric.error_count += 1

        metric.min_response_time = min(metric.min_response_time, response_time)
        metric.max_response_time = max(metric.max_response_time, response_time)

        # 保留最近1000次请求的响应时间
        metric.response_times.append(response_time)
        if len(metric.response_times) > 1000:
            metric.response_times.pop(0)

    def get_statistics(self, endpoint: str) -> Dict[str, Any]:
        """获取统计信息"""
        metric = self.metrics[endpoint]

        if metric.total_requests == 0:
            return {"error": "No data available"}

        response_times = metric.response_times
        avg_response_time = metric.total_response_time / metric.total_requests

        # 计算百分位数
        if response_times:
            sorted_times = sorted(response_times)
            p50 = sorted_times[len(sorted_times) // 2]
            p95 = sorted_times[int(len(sorted_times) * 0.95)]
            p99 = sorted_times[int(len(sorted_times) * 0.99)]
        else:
            p50 = p95 = p99 = 0

        return {
            "total_requests": metric.total_requests,
            "error_rate": metric.error_count / metric.total_requests,
            "avg_response_time": avg_response_time,
            "min_response_time": metric.min_response_time,
            "max_response_time": metric.max_response_time,
            "p50_response_time": p50,
            "p95_response_time": p95,
            "p99_response_time": p99,
            "rps": metric.total_requests / (time.time() - self.start_time)
        }

    def get_all_statistics(self) -> Dict[str, Dict[str, Any]]:
        """获取所有端点的统计信息"""
        return {
            endpoint: self.get_statistics(endpoint)
            for endpoint in self.metrics.keys()
        }

# 全局指标收集器
metrics_collector = MetricsCollector()

# 装饰器
def monitor_performance(endpoint_name: str = None):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            endpoint = endpoint_name or func.__name__
            start_time = time.time()
            success = True

            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                raise e
            finally:
                response_time = time.time() - start_time
                metrics_collector.record_request(endpoint, response_time, success)

        return wrapper
    return decorator
```

## 🖥️ 硬件优化建议

### CPU优化

```python
# config/cpu_optimization.py
import os
import multiprocessing

class CPUOptimization:
    """CPU优化配置"""

    @staticmethod
    def get_optimal_worker_count() -> int:
        """获取最优工作进程数"""
        cpu_count = multiprocessing.cpu_count()

        # 对于I/O密集型应用
        io_intensive_workers = cpu_count * 2 + 1

        # 对于CPU密集型应用
        cpu_intensive_workers = cpu_count

        # 混合型应用的建议
        return min(io_intensive_workers, 16)  # 限制最大工作进程数

    @staticmethod
    def set_cpu_affinity():
        """设置CPU亲和性"""
        if hasattr(os, 'sched_setaffinity'):
            # 将进程绑定到特定CPU核心
            cpu_cores = list(range(multiprocessing.cpu_count()))
            os.sched_setaffinity(0, cpu_cores)

    @staticmethod
    def optimize_for_trading():
        """交易应用CPU优化"""
        # 设置进程优先级
        try:
            os.nice(-10)  # 提高进程优先级（需要权限）
        except PermissionError:
            pass

        # 设置CPU调度策略（Linux）
        try:
            import ctypes
            import ctypes.util

            libc = ctypes.CDLL(ctypes.util.find_library('c'))
            SCHED_FIFO = 1

            class SchedParam(ctypes.Structure):
                _fields_ = [('sched_priority', ctypes.c_int)]

            param = SchedParam()
            param.sched_priority = 50

            # 设置实时调度策略（需要权限）
            libc.sched_setscheduler(0, SCHED_FIFO, ctypes.byref(param))
        except:
            pass
```

### 内存优化

```python
# config/memory_optimization.py
import mmap
import os
from typing import BinaryIO

class MemoryOptimization:
    """内存优化工具"""

    @staticmethod
    def use_memory_mapped_file(file_path: str, size: int) -> mmap.mmap:
        """使用内存映射文件"""
        with open(file_path, 'r+b') as f:
            return mmap.mmap(f.fileno(), size)

    @staticmethod
    def optimize_python_memory():
        """优化Python内存使用"""
        import gc

        # 调整垃圾回收阈值
        gc.set_threshold(700, 10, 10)

        # 禁用循环垃圾回收器（如果确定没有循环引用）
        # gc.disable()

    @staticmethod
    def configure_huge_pages():
        """配置大页内存"""
        try:
            # 启用透明大页
            with open('/sys/kernel/mm/transparent_hugepage/enabled', 'w') as f:
                f.write('always')
        except:
            pass
```

### 网络优化

```python
# config/network_optimization.py
import socket
import asyncio

class NetworkOptimization:
    """网络优化配置"""

    @staticmethod
    def optimize_socket(sock: socket.socket):
        """优化socket配置"""
        # 启用TCP_NODELAY，禁用Nagle算法
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        # 设置socket缓冲区大小
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65536)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)

        # 启用keep-alive
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

    @staticmethod
    def configure_asyncio_policy():
        """配置asyncio策略"""
        if hasattr(asyncio, 'WindowsProactorEventLoopPolicy'):
            # Windows上使用ProactorEventLoop
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        elif hasattr(asyncio, 'unix_events'):
            # Linux上使用epoll
            pass  # 默认已经是最优的

    @staticmethod
    def tune_kernel_parameters():
        """调优内核网络参数（需要root权限）"""
        kernel_params = {
            '/proc/sys/net/core/rmem_max': '134217728',
            '/proc/sys/net/core/wmem_max': '134217728',
            '/proc/sys/net/ipv4/tcp_rmem': '4096 16384 134217728',
            '/proc/sys/net/ipv4/tcp_wmem': '4096 65536 134217728',
            '/proc/sys/net/core/netdev_max_backlog': '5000',
            '/proc/sys/net/ipv4/tcp_window_scaling': '1',
            '/proc/sys/net/ipv4/tcp_congestion_control': 'bbr'
        }

        for param, value in kernel_params.items():
            try:
                with open(param, 'w') as f:
                    f.write(value)
            except:
                pass
```

---

**相关文档**：
- [部署指南](./DEPLOYMENT.md) - 生产环境部署优化
- [监控配置](./DEPLOYMENT.md#监控配置) - 性能监控设置
- [故障排查](./TROUBLESHOOTING.md) - 性能问题诊断
- [系统架构](./ARCHITECTURE.md) - 架构设计原理

**性能优化检查清单**：

- [ ] 数据库查询优化和索引配置
- [ ] 连接池参数调优
- [ ] 缓存策略实施
- [ ] 异步并发控制
- [ ] 内存使用监控
- [ ] 网络连接优化
- [ ] 硬件资源配置
- [ ] 性能指标监控

*定期运行性能基准测试，持续优化系统性能。*
