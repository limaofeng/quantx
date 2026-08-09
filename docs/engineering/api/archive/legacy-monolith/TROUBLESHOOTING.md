# 🐛 QuantX 故障排查指南

本文档提供 QuantX 量化交易系统常见问题的诊断和解决方案，帮助快速定位和修复系统故障。

## 📋 目录

- [常用诊断工具](#常用诊断工具)
- [启动相关问题](#启动相关问题)
- [数据库连接问题](#数据库连接问题)
- [API 接口问题](#api-接口问题)
- [交易执行问题](#交易执行问题)
- [性能问题](#性能问题)
- [网络连接问题](#网络连接问题)
- [策略运行问题](#策略运行问题)
- [工作流调度问题](#工作流调度问题)
- [监控和日志](#监控和日志)
- [安全相关问题](#安全相关问题)
- [环境配置问题](#环境配置问题)

## 🛠️ 常用诊断工具

### 系统信息检查

```bash
# 检查系统资源
htop
free -h
df -h
iostat -x 1

# 检查网络连接
netstat -tulpn | grep :8000
ss -tuln | grep :8000

# 检查进程状态
ps aux | grep quantx
systemctl status quantx

# 检查日志
journalctl -u quantx -f
tail -f /opt/quantx/logs/quantx.log
```

### 健康检查脚本

```bash
#!/bin/bash
# scripts/health_check.sh

echo "🔍 QuantX 系统健康检查"

# 检查服务状态
echo "📋 服务状态:"
systemctl is-active quantx && echo "✅ QuantX 服务运行正常" || echo "❌ QuantX 服务异常"
systemctl is-active postgresql && echo "✅ PostgreSQL 运行正常" || echo "❌ PostgreSQL 异常"
systemctl is-active redis && echo "✅ Redis 运行正常" || echo "❌ Redis 异常"
systemctl is-active nginx && echo "✅ Nginx 运行正常" || echo "❌ Nginx 异常"

# 检查端口
echo "🔌 端口检查:"
nc -z localhost 8000 && echo "✅ API 端口 8000 正常" || echo "❌ API 端口 8000 异常"
nc -z localhost 5432 && echo "✅ PostgreSQL 端口 5432 正常" || echo "❌ PostgreSQL 端口 5432 异常"
nc -z localhost 6379 && echo "✅ Redis 端口 6379 正常" || echo "❌ Redis 端口 6379 异常"

# 检查 API 健康
echo "🌐 API 健康检查:"
if curl -f --max-time 10 http://localhost:8000/health > /dev/null 2>&1; then
    echo "✅ API 健康检查通过"
else
    echo "❌ API 健康检查失败"
fi

# 检查数据库连接
echo "🗄️ 数据库连接:"
if psql -h localhost -U quantx -d quantx -c "SELECT 1;" > /dev/null 2>&1; then
    echo "✅ 数据库连接正常"
else
    echo "❌ 数据库连接失败"
fi

# 检查磁盘空间
echo "💾 磁盘空间检查:"
df -h | awk '$5 > 80 {print "⚠️ " $6 " 磁盘使用率过高: " $5}'

# 检查内存使用
echo "🧠 内存使用:"
free -h | awk 'NR==2{printf "内存使用率: %.2f%%\n", $3/$2*100}'

echo "🔍 健康检查完成"
```

## 🚀 启动相关问题

### 问题：服务无法启动

**症状**：
- `systemctl start quantx` 失败
- 服务立即退出
- 日志显示启动错误

**诊断步骤**：

```bash
# 查看详细错误信息
journalctl -u quantx -n 50 --no-pager

# 检查配置文件
python -c "from config.settings import settings; print(settings.dict())"

# 手动启动测试
cd /opt/quantx/app
python main.py

# 检查依赖
pip check
```

**常见原因和解决方案**：

1. **环境变量配置错误**
```bash
# 检查环境变量
env | grep QUANTX
cat .env

# 修复：更新环境变量
export DATABASE_URL="postgresql://user:pass@localhost/db"
```

2. **端口被占用**
```bash
# 查找占用进程
sudo lsof -i :8000
sudo kill -9 <PID>

# 或更改端口
export API_PORT=8001
```

3. **权限问题**
```bash
# 修复权限
sudo chown -R quantx:quantx /opt/quantx
sudo chmod +x /opt/quantx/app/main.py
```

4. **依赖缺失**
```bash
# 重新安装依赖
poetry install
```

### 问题：服务启动后立即崩溃

**诊断**：
```bash
# 查看核心转储
coredumpctl list
coredumpctl info <PID>

# 检查内存问题
dmesg | grep -i "killed process"

# 运行调试模式
python -X dev main.py
```

**解决方案**：
- 增加内存限制
- 检查内存泄漏
- 优化配置参数

## 🗄️ 数据库连接问题

### 问题：无法连接到 PostgreSQL

**症状**：
- 连接超时
- 认证失败
- 连接池耗尽

**诊断步骤**：

```bash
# 测试连接
psql -h localhost -U quantx -d quantx

# 检查服务状态
systemctl status postgresql

# 查看连接数
psql -c "SELECT count(*) FROM pg_stat_activity;"

# 检查配置
cat /etc/postgresql/*/main/postgresql.conf | grep -E "listen_addresses|max_connections"
```

**解决方案**：

1. **认证问题**
```bash
# 检查 pg_hba.conf
sudo cat /etc/postgresql/*/main/pg_hba.conf

# 添加认证规则
echo "local   quantx   quantx   md5" | sudo tee -a /etc/postgresql/*/main/pg_hba.conf
sudo systemctl reload postgresql
```

2. **连接数限制**
```sql
-- 查看当前连接
SELECT count(*), state FROM pg_stat_activity GROUP BY state;

-- 终止空闲连接
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
WHERE state = 'idle' AND query_start < now() - interval '1 hour';
```

3. **连接池配置**
```python
# config/database.py
DATABASE_POOL_SIZE = 20
DATABASE_MAX_OVERFLOW = 30
DATABASE_POOL_TIMEOUT = 30
DATABASE_POOL_RECYCLE = 3600
```

### 问题：数据库性能慢

**诊断**：
```sql
-- 查看慢查询
SELECT query, mean_exec_time, calls
FROM pg_stat_statements
ORDER BY mean_exec_time DESC LIMIT 10;

-- 查看锁等待
SELECT * FROM pg_locks WHERE NOT granted;

-- 查看表大小
SELECT tablename, pg_size_pretty(pg_total_relation_size(tablename::regclass))
FROM pg_tables WHERE schemaname = 'public';
```

**解决方案**：
- 添加索引
- 优化查询
- 运行 VACUUM ANALYZE
- 调整数据库参数

## 🌐 API 接口问题

### 问题：API 响应慢或超时

**症状**：
- 请求超时
- 响应时间过长
- 间歇性 502/503 错误

**诊断步骤**：

```bash
# 测试 API 响应时间
curl -w "@curl-format.txt" -s -o /dev/null http://localhost:8000/health

# curl-format.txt 内容：
#     time_namelookup:  %{time_namelookup}\n
#        time_connect:  %{time_connect}\n
#     time_appconnect:  %{time_appconnect}\n
#    time_pretransfer:  %{time_pretransfer}\n
#       time_redirect:  %{time_redirect}\n
#  time_starttransfer:  %{time_starttransfer}\n
#                     ----------\n
#          time_total:  %{time_total}\n

# 检查进程状态
ps aux | grep gunicorn
top -p $(pgrep -d',' -f gunicorn)

# 检查网络
ss -tuln | grep :8000
```

**解决方案**：

1. **增加工作进程**
```python
# gunicorn.conf.py
workers = multiprocessing.cpu_count() * 2 + 1
worker_connections = 1000
```

2. **启用缓存**
```python
# 添加 Redis 缓存
@cache.memoize(timeout=300)
def get_market_data(symbol):
    return expensive_operation(symbol)
```

3. **数据库连接优化**
```python
# 增加连接池
engine = create_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=30
)
```

### 问题：GraphQL 查询错误

**症状**：
- 查询语法错误
- 字段不存在
- 权限拒绝

**诊断**：
```bash
# 检查 GraphQL schema
curl -X POST http://localhost:8000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ __schema { types { name } } }"}'

# 测试简单查询
curl -X POST http://localhost:8000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ health }"}'
```

**解决方案**：
- 验证查询语法
- 检查字段权限
- 更新 schema 定义

## 💼 交易执行问题

### 问题：订单执行失败

**症状**：
- 订单被拒绝
- 余额不足错误
- 交易接口连接失败

**诊断步骤**：

```bash
# 查看交易日志
grep -i "order\|trade" /opt/quantx/logs/quantx.log | tail -20

# 检查账户状态
curl -X POST http://localhost:8000/graphql \
  -H "Content-Type: application/json" \
  -d '{"query": "{ account { availableCash totalValue } }"}'

# 测试 XTQuant 连接
python -c "from miniqmt.xtquant_utils import test_connection; test_connection()"
```

**常见问题和解决方案**：

1. **资金不足**
```python
# 检查账户资金
def check_sufficient_funds(order_value, account_cash):
    if order_value > account_cash:
        raise InsufficientFundsError(f"资金不足：需要 {order_value}，可用 {account_cash}")
```

2. **XTQuant 连接问题**
```python
# 重新连接 XTQuant
from miniqmt.xtquant_manager import XTQuantManager

manager = XTQuantManager()
await manager.reconnect()
```

3. **市场关闭**
```python
# 检查交易时间
def is_trading_time():
    now = datetime.now()
    return 9 <= now.hour < 15  # 简化版本
```

### 问题：策略意图不准确

**诊断**：
```python
# 启用策略调试模式
strategy.debug_mode = True

# 检查指标计算
prices = [10, 11, 12, 11, 10, 9, 10, 11, 12, 13]
ma5 = MA(period=5).calculate(prices)
print(f"MA5: {ma5}")

# 记录意图生成
logger.info(f"生成意图: {intent.direction} at {intent.limit_price_hint}")
```

**解决方案**：
- 验证历史数据质量
- 检查指标计算逻辑
- 调整策略参数

## ⚡ 性能问题

### 问题：系统响应慢

**症状**：
- CPU 使用率高
- 内存使用率高
- 磁盘 I/O 高

**诊断工具**：

```bash
# CPU 分析
top -p $(pgrep -d',' python)
htop

# 内存分析
free -h
cat /proc/meminfo

# 磁盘 I/O 分析
iostat -x 1
iotop

# 网络分析
iftop
nethogs
```

**Python 性能分析**：

```python
# 使用 cProfile
python -m cProfile -o profile.stats main.py

# 使用 py-spy (生产环境)
py-spy record -o profile.svg -d 60 -p <PID>

# 内存分析
import tracemalloc
tracemalloc.start()
# ... 运行代码 ...
snapshot = tracemalloc.take_snapshot()
top_stats = snapshot.statistics('lineno')
for stat in top_stats[:10]:
    print(stat)
```

**解决方案**：

1. **优化数据库查询**
```python
# 使用索引
class Order(Base):
    __tablename__ = 'orders'
    symbol = Column(String, index=True)
    create_time = Column(DateTime, index=True)

# 批量操作
session.bulk_insert_mappings(Order, order_data)
```

2. **缓存热点数据**
```python
@lru_cache(maxsize=1000)
def get_instrument_info(symbol):
    return expensive_database_query(symbol)
```

3. **异步处理**
```python
# 使用异步任务
@celery.task
def process_market_data(data):
    # 异步处理逻辑
    pass
```

### 问题：内存泄漏

**诊断**：
```bash
# 监控内存使用
while true; do
    ps -p $(pgrep python) -o pid,vsz,rss,pcpu,pmem,cmd
    sleep 60
done

# 使用 memory_profiler
pip install memory-profiler
python -m memory_profiler script.py
```

**解决方案**：
- 及时关闭数据库连接
- 避免循环引用
- 清理大对象引用

## 🌐 网络连接问题

### 问题：外部 API 连接失败

**症状**：
- 连接超时
- DNS 解析失败
- SSL 证书错误

**诊断步骤**：

```bash
# 测试网络连接
ping api.external-service.com
telnet api.external-service.com 443
curl -I https://api.external-service.com

# DNS 解析测试
nslookup api.external-service.com
dig api.external-service.com

# SSL 证书检查
openssl s_client -connect api.external-service.com:443 -servername api.external-service.com
```

**解决方案**：

1. **配置代理**
```python
import httpx

async with httpx.AsyncClient(
    proxies="http://proxy.company.com:8080",
    verify=False  # 仅测试环境
) as client:
    response = await client.get("https://api.external-service.com")
```

2. **重试机制**
```python
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
async def fetch_external_data():
    async with httpx.AsyncClient() as client:
        response = await client.get("https://api.external-service.com")
        return response.json()
```

3. **超时配置**
```python
timeout = httpx.Timeout(10.0, connect=5.0)
async with httpx.AsyncClient(timeout=timeout) as client:
    response = await client.get(url)
```

## 📈 策略运行问题

### 问题：策略不执行交易

**诊断检查清单**：

```python
# 1. 检查策略状态
strategy_status = strategy_manager.get_strategy_status(strategy_id)
print(f"策略状态: {strategy_status}")

# 2. 检查市场数据
market_data = await market_service.get_latest_market_data(symbol)
print(f"最新数据: {market_data}")

# 3. 检查交易权限
trading_enabled = settings.TRADING_ENABLED
print(f"交易启用: {trading_enabled}")

# 4. 检查资金状态
account = await trading_service.get_account_info()
print(f"可用资金: {account.available_cash}")

# 5. 检查策略逻辑
output = await strategy.step(strategy_input)
if output.trade_intents:
    print("应该生成交易意图")
else:
    print("不满足交易意图条件")
```

**常见问题**：

1. **策略暂停**
```python
# 检查策略状态
if strategy.status == StrategyStatus.PAUSED:
    strategy.resume()
```

2. **数据延迟**
```python
# 检查数据时效性
data_age = datetime.now() - market_data.timestamp
if data_age > timedelta(minutes=5):
    logger.warning(f"数据延迟: {data_age}")
```

3. **意图过滤**
```python
# 检查意图过滤条件
if intent.confidence < strategy.min_intent_confidence:
    logger.info(f"意图置信度不足: {intent.confidence}")
```

## 🔄 工作流调度问题

### 问题：Prefect 流程不执行

**症状**：
- 流程长时间处于等待状态
- 任务执行失败
- 调度器停止工作

**诊断步骤**：

```bash
# 检查 Prefect 服务
prefect server status

# 查看流程状态
prefect flow ls
prefect deployment ls

# 查看任务执行历史
prefect flow-run ls --limit 10

# 检查工作池
prefect work-pool ls
```

**解决方案**：

1. **重启 Prefect 服务**
```bash
prefect server stop
prefect server start
```

2. **检查工作池配置**
```python
from prefect.deployments import Deployment

deployment = Deployment.build_from_flow(
    flow=my_flow,
    name="my-deployment",
    work_pool_name="default",
    schedule={"interval": 3600}  # 每小时执行
)
```

3. **任务重试配置**
```python
from prefect import task
from prefect.tasks import exponential_backoff

@task(retries=3, retry_delay_seconds=exponential_backoff(backoff_factor=2))
def unreliable_task():
    # 可能失败的任务
    pass
```

## 📊 监控和日志

### 日志配置优化

```python
# config/logging.py
import logging
from logging.handlers import RotatingFileHandler

def setup_logging():
    # 创建格式器
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # 文件处理器
    file_handler = RotatingFileHandler(
        'logs/quantx.log',
        maxBytes=100*1024*1024,  # 100MB
        backupCount=10
    )
    file_handler.setFormatter(formatter)

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # 根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)
```

### 关键日志检查

```bash
# 检查错误日志
grep -i error /opt/quantx/logs/quantx.log | tail -10

# 检查交易日志
grep -i "order\|trade" /opt/quantx/logs/quantx.log | tail -10

# 检查数据库连接
grep -i "database\|connection" /opt/quantx/logs/quantx.log | tail -10

# 实时监控日志
tail -f /opt/quantx/logs/quantx.log | grep -E "(ERROR|WARNING|order|trade)"
```

### 性能监控脚本

```bash
#!/bin/bash
# scripts/monitor_performance.sh

LOGFILE="/opt/quantx/logs/performance.log"

while true; do
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

    # CPU 使用率
    CPU_USAGE=$(top -bn1 | grep "Cpu(s)" | awk '{print $2}' | cut -d'%' -f1)

    # 内存使用率
    MEM_USAGE=$(free | grep Mem | awk '{printf("%.2f", ($3/$2) * 100.0)}')

    # 磁盘使用率
    DISK_USAGE=$(df -h / | awk 'NR==2{print $5}' | cut -d'%' -f1)

    # API 响应时间
    API_RESPONSE=$(curl -w "%{time_total}" -s -o /dev/null http://localhost:8000/health)

    echo "$TIMESTAMP,CPU:$CPU_USAGE%,MEM:$MEM_USAGE%,DISK:$DISK_USAGE%,API:${API_RESPONSE}s" >> $LOGFILE

    sleep 60
done
```

## 🔒 安全相关问题

### 问题：认证失败

**症状**：
- JWT token 无效
- 权限被拒绝
- 登录失败

**诊断**：
```python
# 验证 JWT token
import jwt
from config.settings import settings

def verify_token(token):
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=['HS256'])
        return payload
    except jwt.ExpiredSignatureError:
        print("Token 已过期")
    except jwt.InvalidTokenError:
        print("Token 无效")
```

**解决方案**：
- 检查 JWT 密钥配置
- 验证 token 有效期
- 更新客户端 token

### 问题：SSL 证书错误

**诊断**：
```bash
# 检查证书有效期
openssl x509 -in /etc/ssl/certs/quantx.crt -text -noout | grep -A2 "Validity"

# 检查证书链
openssl s_client -connect api.quantx.com:443 -verify_return_error
```

**解决方案**：
- 更新证书
- 配置证书链
- 检查域名匹配

## ⚙️ 环境配置问题

### 问题：环境变量未生效

**诊断**：
```bash
# 检查环境变量
env | grep QUANTX
printenv DATABASE_URL

# 检查配置文件
cat .env
python -c "from config.settings import settings; print(settings.DATABASE_URL)"
```

**解决方案**：
- 重启服务加载新配置
- 检查环境变量优先级
- 验证配置文件格式

### 问题：依赖版本冲突

**诊断**：
```bash
# 检查依赖冲突
poetry check

# 查看依赖树
pip show <package>
pipdeptree

# 检查版本
pip list | grep <package>
```

**解决方案**：
```bash
# 更新依赖
poetry add <package>@latest

# 固定版本
poetry add package==1.2.3

# 创建并激活新的 Poetry 虚拟环境
poetry env use python3.10  # 使用特定的 Python 版本
poetry install  # 安装所有依赖

# 或者使用开发模式安装
poetry install -E dev  # 安装开发依赖

# 激活虚拟环境
poetry shell
```

## 📞 获取帮助

### 自助诊断检查清单

在寻求帮助前，请完成以下检查：

- [ ] 运行系统健康检查脚本
- [ ] 查看最近的错误日志
- [ ] 确认配置文件正确
- [ ] 验证网络连接
- [ ] 检查系统资源使用
- [ ] 测试基本功能

### 收集诊断信息

```bash
#!/bin/bash
# scripts/collect_diagnostics.sh

REPORT_FILE="quantx_diagnostics_$(date +%Y%m%d_%H%M%S).txt"

echo "QuantX 诊断报告 - $(date)" > $REPORT_FILE
echo "================================" >> $REPORT_FILE

echo -e "\n系统信息:" >> $REPORT_FILE
uname -a >> $REPORT_FILE
lsb_release -a >> $REPORT_FILE

echo -e "\n服务状态:" >> $REPORT_FILE
systemctl status quantx >> $REPORT_FILE 2>&1

echo -e "\n错误日志 (最近 50 行):" >> $REPORT_FILE
tail -50 /opt/quantx/logs/quantx.log | grep -i error >> $REPORT_FILE

echo -e "\n网络连接:" >> $REPORT_FILE
netstat -tulpn | grep -E ":8000|:5432|:6379" >> $REPORT_FILE

echo -e "\n资源使用:" >> $REPORT_FILE
free -h >> $REPORT_FILE
df -h >> $REPORT_FILE

echo "诊断报告已生成: $REPORT_FILE"
```

### 联系支持

提交问题时请包含：

1. **问题描述**：详细描述症状和重现步骤
2. **环境信息**：操作系统、Python 版本、部署方式
3. **错误日志**：相关的错误日志和堆栈跟踪
4. **配置信息**：隐藏敏感信息的配置文件
5. **诊断报告**：运行诊断脚本的输出

---

**相关文档**：
- [部署指南](./DEPLOYMENT.md)
- [性能优化](./PERFORMANCE.md)
- [系统架构](./ARCHITECTURE.md)
- [API 文档](./API.md)

**有用命令快速参考**：
```bash
# 重启所有服务
sudo systemctl restart quantx postgresql redis nginx

# 查看实时日志
tail -f /opt/quantx/logs/quantx.log

# 检查端口占用
netstat -tulpn | grep :8000

# 数据库连接测试
psql -h localhost -U quantx -d quantx -c "SELECT version();"

# API 健康检查
curl http://localhost:8000/health
```

*定期运行健康检查脚本可以帮助提前发现和预防问题。*
