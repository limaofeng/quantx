# 🚀 QuantX 部署指南

本文档详细介绍 QuantX 量化交易系统的部署方案，包括开发环境、测试环境和生产环境的配置与部署。

## 📋 目录

- [系统要求](#系统要求)
- [环境配置](#环境配置)
- [开发环境部署](#开发环境部署)
- [测试环境部署](#测试环境部署)
- [生产环境部署](#生产环境部署)
- [Docker 部署](#docker-部署)
- [数据库配置](#数据库配置)
- [性能优化](#性能优化)
- [监控配置](#监控配置)
- [安全配置](#安全配置)
- [故障恢复](#故障恢复)

## 💻 系统要求

### 最低配置

- **CPU**: 4核心 2.5GHz
- **内存**: 8GB RAM
- **存储**: 100GB SSD
- **网络**: 100Mbps 带宽
- **操作系统**: Ubuntu 20.04+ / CentOS 8+ / Windows 10+

### 推荐配置

- **CPU**: 8核心 3.0GHz
- **内存**: 16GB RAM
- **存储**: 500GB NVMe SSD
- **网络**: 1Gbps 带宽
- **操作系统**: Ubuntu 22.04 LTS

### 生产环境配置

- **CPU**: 16核心 3.5GHz
- **内存**: 32GB RAM
- **存储**: 1TB NVMe SSD + 2TB HDD
- **网络**: 10Gbps 带宽
- **高可用**: 多节点集群部署

## 🛠️ 环境配置

### Python 环境

```bash
# 安装 Python 3.9+
sudo apt update
sudo apt install python3.9 python3.9-venv python3.9-dev

# 创建虚拟环境
python3.9 -m venv venv
source venv/bin/activate  # Linux/Mac
# 或 venv\Scripts\activate  # Windows

# 升级 pip
pip install --upgrade pip setuptools wheel
```

### 系统依赖

```bash
# Ubuntu/Debian
sudo apt install -y \
    build-essential \
    libpq-dev \
    libssl-dev \
    libffi-dev \
    redis-server \
    git \
    curl \
    wget

# CentOS/RHEL
sudo yum groupinstall -y "Development Tools"
sudo yum install -y \
    postgresql-devel \
    openssl-devel \
    libffi-devel \
    redis \
    git \
    curl \
    wget
```

### Node.js (可选，用于前端工具)

```bash
# 安装 Node.js 18+
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt-get install -y nodejs
```

## 🏗️ 开发环境部署

### 1. 项目初始化

```bash
# 克隆项目
git clone https://github.com/yourusername/quantx.git
cd quantx/backend

# 安装依赖
# 使用 pyproject.toml 安装项目及开发依赖
pip install -e .
pip install -e .[dev]  # 安装开发依赖

# 或使用 Poetry（推荐）
poetry install  # 安装所有依赖
poetry install -E dev  # 安装开发依赖

# 配置环境变量
cp .env.example .env.development
```

### 2. 环境变量配置

```bash
# .env.development
ENV=development
DEBUG=true

# 数据库配置
DATABASE_URL=postgresql://quantx:password@localhost:5432/quantx_dev
INFLUXDB_URL=http://localhost:8086
INFLUXDB_TOKEN=your_influxdb_token
INFLUXDB_ORG=quantx
INFLUXDB_BUCKET=market_data

# Redis 配置
REDIS_URL=redis://localhost:6379/0

# API 配置
API_HOST=0.0.0.0
API_PORT=8000
API_WORKERS=1

# 日志配置
LOG_LEVEL=DEBUG
LOG_FILE=logs/quantx.log

# XTQuant 配置
# miniQMT 默认启用；连接失败会降级为未连接状态，不阻塞 API 启动。
XTQUANT_ACCOUNT=
XTQUANT_PASSWORD=

# 安全配置
JWT_SECRET=your_jwt_secret_key
CORS_ORIGINS=http://localhost:3000,http://localhost:8080

# 交易配置
TRADING_ENABLED=false
PAPER_TRADING=true
```

### 3. 数据库初始化

```bash
# 启动 PostgreSQL
sudo systemctl start postgresql

# 创建数据库
sudo -u postgres createdb quantx_dev
sudo -u postgres createuser quantx
sudo -u postgres psql -c "ALTER USER quantx PASSWORD 'password';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE quantx_dev TO quantx;"

# 运行迁移
alembic upgrade head
```

### 4. 启动服务

```bash
# 启动开发服务器
python main.py

# 或使用热重载
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 启动 Prefect 服务器（可选）
prefect server start

# 后台运行
nohup python main.py > logs/quantx.log 2>&1 &
```

### 5. 验证部署

```bash
# 健康检查
curl http://localhost:8000/health

# API 文档
curl http://localhost:8000/docs

# GraphQL Playground
# 访问 http://localhost:8000/graphql
```

## 🧪 测试环境部署

### 1. 环境配置

```bash
# .env.testing
ENV=testing
DEBUG=false

# 测试数据库
DATABASE_URL=postgresql://quantx:password@localhost:5432/quantx_test
INFLUXDB_BUCKET=test_market_data

# 禁用真实交易
TRADING_ENABLED=false
ENABLE_REAL_TRADING=false

# 测试配置
TEST_MODE=true
MOCK_EXTERNAL_APIS=true
```

### 2. CI/CD 配置

```yaml
# .github/workflows/test.yml
name: Test and Deploy

on:
  push:
    branches: [ main, develop ]
  pull_request:
    branches: [ main ]

jobs:
  test:
    runs-on: ubuntu-latest

    services:
      postgres:
        image: postgres:13
        env:
          POSTGRES_DB: quantx_test
          POSTGRES_USER: quantx
          POSTGRES_PASSWORD: password
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

      redis:
        image: redis:6
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
    - uses: actions/checkout@v3

    - name: Set up Python
      uses: actions/setup-python@v4
      with:
        python-version: '3.9'

    - name: Install dependencies
      run: |
        poetry install
        pip install -r requirements-dev.txt

    - name: Run tests
      env:
        DATABASE_URL: postgresql://quantx:password@localhost:5432/quantx_test
        REDIS_URL: redis://localhost:6379
        ENV: testing
      run: |
        python -m pytest tests/ --cov=. --cov-report=xml

    - name: Upload coverage
      uses: codecov/codecov-action@v3
      with:
        file: ./coverage.xml
```

### 3. 自动化部署脚本

```bash
#!/bin/bash
# scripts/deploy_test.sh

set -e

echo "🚀 开始部署测试环境"

# 更新代码
git pull origin main

# 更新依赖
poetry install

# 运行迁移
alembic upgrade head

# 运行测试
python -m pytest tests/ --tb=short

# 重启服务
sudo systemctl restart quantx-test

# 验证部署
sleep 5
curl -f http://localhost:8001/health || exit 1

echo "✅ 测试环境部署完成"
```

## 🏭 生产环境部署

### 1. 系统准备

```bash
# 创建系统用户
sudo useradd -r -m -s /bin/bash quantx
sudo usermod -aG sudo quantx

# 创建目录结构
sudo mkdir -p /opt/quantx/{app,logs,data,backups}
sudo chown -R quantx:quantx /opt/quantx

# 配置防火墙
sudo ufw allow 22/tcp     # SSH
sudo ufw allow 80/tcp     # HTTP
sudo ufw allow 443/tcp    # HTTPS
sudo ufw allow 8000/tcp   # API
sudo ufw enable
```

### 2. 生产环境配置

```bash
# .env.production
ENV=production
DEBUG=false

# 数据库配置（高可用）
DATABASE_URL=postgresql://quantx:strong_password@prod-db-cluster:5432/quantx_prod
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=30

# Redis 集群
REDIS_URL=redis://prod-redis-cluster:6379/0
REDIS_CLUSTER_NODES=redis1:6379,redis2:6379,redis3:6379

# InfluxDB 集群
INFLUXDB_URL=https://prod-influxdb:8086
INFLUXDB_TOKEN=production_token
INFLUXDB_ORG=quantx_prod
INFLUXDB_BUCKET=market_data_prod

# API 配置
API_HOST=0.0.0.0
API_PORT=8000
API_WORKERS=8

# 安全配置
JWT_SECRET=very_strong_jwt_secret_key_for_production
CORS_ORIGINS=https://app.quantx.com,https://admin.quantx.com

# SSL 配置
SSL_CERT_PATH=/etc/ssl/certs/quantx.crt
SSL_KEY_PATH=/etc/ssl/private/quantx.key

# 交易配置
TRADING_ENABLED=true
XTQUANT_ACCOUNT=production_account
XTQUANT_PASSWORD=production_password

# 监控配置
PROMETHEUS_ENABLED=true
PROMETHEUS_PORT=9090

# 日志配置
LOG_LEVEL=INFO
LOG_FILE=/opt/quantx/logs/quantx.log
LOG_ROTATION=daily
LOG_RETENTION=30
```

### 3. 负载均衡配置

```nginx
# /etc/nginx/sites-available/quantx
upstream quantx_api {
    server 127.0.0.1:8000;
    server 127.0.0.1:8001;
    server 127.0.0.1:8002;
    server 127.0.0.1:8003;
}

server {
    listen 80;
    server_name api.quantx.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name api.quantx.com;

    ssl_certificate /etc/ssl/certs/quantx.crt;
    ssl_certificate_key /etc/ssl/private/quantx.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512;

    # 安全头
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";

    # API 代理
    location / {
        proxy_pass http://quantx_api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket 支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # 超时配置
        proxy_connect_timeout 30s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }

    # 静态文件
    location /static/ {
        alias /opt/quantx/app/static/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # 健康检查
    location /health {
        access_log off;
        return 200 "healthy\n";
        add_header Content-Type text/plain;
    }
}
```

### 4. Systemd 服务配置

```ini
# /etc/systemd/system/quantx.service
[Unit]
Description=QuantX API Service
After=network.target postgresql.service redis.service
Wants=postgresql.service redis.service

[Service]
Type=forking
User=quantx
Group=quantx
WorkingDirectory=/opt/quantx/app
Environment=PATH=/opt/quantx/venv/bin
ExecStart=/opt/quantx/venv/bin/gunicorn main:app \
    --bind 0.0.0.0:8000 \
    --workers 8 \
    --worker-class uvicorn.workers.UvicornWorker \
    --worker-connections 1000 \
    --max-requests 10000 \
    --max-requests-jitter 1000 \
    --timeout 30 \
    --keep-alive 2 \
    --pid /opt/quantx/app/quantx.pid \
    --daemon
ExecReload=/bin/kill -s HUP $MAINPID
ExecStop=/bin/kill -s TERM $MAINPID
PIDFile=/opt/quantx/app/quantx.pid
Restart=always
RestartSec=10

# 安全配置
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/quantx/logs /opt/quantx/data

[Install]
WantedBy=multi-user.target
```

### 5. 生产部署脚本

```bash
#!/bin/bash
# scripts/deploy_production.sh

set -e

DEPLOY_DIR="/opt/quantx"
APP_DIR="$DEPLOY_DIR/app"
BACKUP_DIR="$DEPLOY_DIR/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "🚀 开始生产环境部署"

# 备份当前版本
if [ -d "$APP_DIR" ]; then
    echo "📦 备份当前版本"
    sudo -u quantx tar -czf "$BACKUP_DIR/quantx_backup_$TIMESTAMP.tar.gz" -C "$DEPLOY_DIR" app
fi

# 下载新版本
echo "📥 下载新版本"
cd /tmp
git clone --depth 1 https://github.com/yourusername/quantx.git quantx_deploy
cd quantx_deploy/api

# 安装依赖
echo "📦 安装依赖"
sudo -u quantx /opt/quantx/venv/bin/poetry install

# 运行测试
echo "🧪 运行测试"
sudo -u quantx ENV=testing /opt/quantx/venv/bin/python -m pytest tests/ --tb=short

# 部署新版本
echo "📋 部署新版本"
sudo systemctl stop quantx
sudo -u quantx rsync -av --delete /tmp/quantx_deploy/api/ "$APP_DIR/"

# 运行迁移
echo "🗃️ 运行数据库迁移"
cd "$APP_DIR"
sudo -u quantx /opt/quantx/venv/bin/alembic upgrade head

# 重启服务
echo "🔄 重启服务"
sudo systemctl start quantx
sudo systemctl reload nginx

# 验证部署
echo "✅ 验证部署"
sleep 10
curl -f https://api.quantx.com/health || {
    echo "❌ 部署验证失败，回滚..."
    sudo systemctl stop quantx
    sudo -u quantx tar -xzf "$BACKUP_DIR/quantx_backup_$TIMESTAMP.tar.gz" -C "$DEPLOY_DIR"
    sudo systemctl start quantx
    exit 1
}

# 清理
rm -rf /tmp/quantx_deploy

echo "✅ 生产环境部署完成"
```

## 🐳 Docker 部署

### 1. Dockerfile

```dockerfile
# Dockerfile
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY pyproject.toml poetry.lock* ./

# 安装 Python 依赖
RUN poetry config virtualenvs.create false && poetry install --no-interaction --no-ansi

# 复制应用代码
COPY . .

# 创建非root用户
RUN useradd --create-home --shell /bin/bash quantx && \
    chown -R quantx:quantx /app
USER quantx

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["gunicorn", "main:app", "--bind", "0.0.0.0:8000", "--workers", "4", "--worker-class", "uvicorn.workers.UvicornWorker"]
```

### 2. Docker Compose

```yaml
# docker-compose.yml
version: '3.8'

services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - ENV=production
      - DATABASE_URL=postgresql://quantx:password@postgres:5432/quantx
      - REDIS_URL=redis://redis:6379/0
      - INFLUXDB_URL=http://influxdb:8086
    depends_on:
      - postgres
      - redis
      - influxdb
    volumes:
      - ./logs:/app/logs
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: '1.0'

  postgres:
    image: postgres:13
    environment:
      - POSTGRES_DB=quantx
      - POSTGRES_USER=quantx
      - POSTGRES_PASSWORD=password
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./backups:/backups
    restart: unless-stopped

  redis:
    image: redis:6-alpine
    command: redis-server --appendonly yes
    volumes:
      - redis_data:/data
    restart: unless-stopped

  influxdb:
    image: influxdb:2.0
    environment:
      - INFLUXDB_DB=quantx
      - INFLUXDB_ADMIN_USER=admin
      - INFLUXDB_ADMIN_PASSWORD=password
    volumes:
      - influxdb_data:/var/lib/influxdb2
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/ssl:ro
    depends_on:
      - app
    restart: unless-stopped

  prometheus:
    image: prom/prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    restart: unless-stopped

  grafana:
    image: grafana/grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana_data:/var/lib/grafana
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
  influxdb_data:
  prometheus_data:
  grafana_data:
```

### 3. Docker 部署脚本

```bash
#!/bin/bash
# scripts/docker_deploy.sh

set -e

echo "🐳 开始 Docker 部署"

# 构建镜像
# 使用多阶段构建优化依赖安装
docker-compose build --no-cache \
    --build-arg PIP_INSTALL_CMD="pip install -e .[dev]" \
    --build-arg POETRY_INSTALL_CMD="poetry install"

# 备份数据
echo "📦 备份数据"
docker-compose exec postgres pg_dump -U quantx quantx > "backup_$(date +%Y%m%d_%H%M%S).sql"

# 停止服务
docker-compose down

# 启动服务
docker-compose up -d

# 等待服务就绪
echo "⏳ 等待服务启动"
sleep 30

# 运行迁移
docker-compose exec app alembic upgrade head

# 验证部署
echo "✅ 验证部署"
curl -f http://localhost:8000/health || exit 1

echo "✅ Docker 部署完成"
```

## 🗄️ 数据库配置

### PostgreSQL 优化配置

```sql
-- postgresql.conf 优化建议
shared_buffers = 1GB                    # 25% of RAM
effective_cache_size = 3GB              # 75% of RAM
maintenance_work_mem = 256MB
checkpoint_completion_target = 0.7
wal_buffers = 16MB
default_statistics_target = 100
random_page_cost = 1.1                  # SSD
effective_io_concurrency = 200          # SSD

# 连接配置
max_connections = 200
superuser_reserved_connections = 3

# 日志配置
log_destination = 'stderr'
logging_collector = on
log_directory = 'pg_log'
log_filename = 'postgresql-%Y-%m-%d_%H%M%S.log'
log_min_duration_statement = 1000       # 记录慢查询
```

### 数据库备份策略

```bash
#!/bin/bash
# scripts/db_backup.sh

BACKUP_DIR="/opt/quantx/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=30

# 创建备份
pg_dump -h localhost -U quantx quantx | gzip > "$BACKUP_DIR/quantx_$TIMESTAMP.sql.gz"

# 删除过期备份
find "$BACKUP_DIR" -name "quantx_*.sql.gz" -mtime +$RETENTION_DAYS -delete

# 备份到远程存储（可选）
# aws s3 cp "$BACKUP_DIR/quantx_$TIMESTAMP.sql.gz" s3://quantx-backups/
```

### InfluxDB 配置

```toml
# influxdb.conf
[http]
enabled = true
bind-address = ":8086"
max-connection-limit = 0
max-enqueued-writes = 1000

[data]
cache-max-memory-size = "1g"
cache-snapshot-memory-size = "25m"
cache-snapshot-write-cold-duration = "10m"

[retention]
enabled = true
check-interval = "30m"
```

## ⚡ 性能优化

### 应用层优化

```python
# config/performance.py
from pydantic import BaseSettings

class PerformanceSettings(BaseSettings):
    # 数据库连接池
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 30
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 3600

    # Redis 配置
    REDIS_POOL_SIZE: int = 50
    REDIS_TIMEOUT: int = 5

    # API 配置
    API_WORKERS: int = 8
    WORKER_CONNECTIONS: int = 1000
    MAX_REQUESTS: int = 10000
    TIMEOUT: int = 30

    # 缓存配置
    CACHE_TTL: int = 300
    CACHE_MAX_SIZE: int = 1000

    class Config:
        env_prefix = "PERF_"
```

### Gunicorn 配置

```python
# gunicorn.conf.py
import multiprocessing

# 服务器配置
bind = "0.0.0.0:8000"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 1000

# 性能配置
max_requests = 10000
max_requests_jitter = 1000
timeout = 30
keepalive = 2
preload_app = True

# 日志配置
accesslog = "/opt/quantx/logs/access.log"
errorlog = "/opt/quantx/logs/error.log"
loglevel = "info"

# 进程管理
user = "quantx"
group = "quantx"
tmp_upload_dir = None
```

### 缓存策略

```python
# services/cache_service.py
import redis
from functools import wraps
import json
import pickle

class CacheService:
    def __init__(self, redis_url: str):
        self.redis = redis.from_url(redis_url)

    def cache_result(self, ttl: int = 300, key_prefix: str = ""):
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # 生成缓存键
                cache_key = f"{key_prefix}:{func.__name__}:{hash(str(args) + str(kwargs))}"

                # 尝试从缓存获取
                cached = self.redis.get(cache_key)
                if cached:
                    return pickle.loads(cached)

                # 执行函数并缓存结果
                result = await func(*args, **kwargs)
                self.redis.setex(cache_key, ttl, pickle.dumps(result))

                return result
            return wrapper
        return decorator

# 使用示例
cache_service = CacheService(redis_url)

@cache_service.cache_result(ttl=60, key_prefix="market_data")
async def get_market_data(symbol: str):
    # 获取市场数据的实现
    pass
```

## 📊 监控配置

### Prometheus 配置

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'quantx-api'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
    scrape_interval: 5s

  - job_name: 'postgres'
    static_configs:
      - targets: ['localhost:9187']

  - job_name: 'redis'
    static_configs:
      - targets: ['localhost:9121']

  - job_name: 'nginx'
    static_configs:
      - targets: ['localhost:9113']
```

### Grafana 仪表板

```json
{
  "dashboard": {
    "title": "QuantX Monitoring",
    "panels": [
      {
        "title": "API Response Time",
        "type": "graph",
        "targets": [
          {
            "expr": "histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m]))",
            "legendFormat": "95th percentile"
          }
        ]
      },
      {
        "title": "Database Connections",
        "type": "graph",
        "targets": [
          {
            "expr": "pg_stat_database_numbackends",
            "legendFormat": "Active connections"
          }
        ]
      }
    ]
  }
}
```

### 应用监控

```python
# monitoring/metrics.py
from prometheus_client import Counter, Histogram, Gauge
import time

# 指标定义
REQUEST_COUNT = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
REQUEST_DURATION = Histogram('http_request_duration_seconds', 'HTTP request duration')
ACTIVE_STRATEGIES = Gauge('active_strategies_total', 'Number of active trading strategies')
DATABASE_CONNECTIONS = Gauge('database_connections_active', 'Active database connections')

def monitor_request(func):
    @wraps(func)
    async def wrapper(request, *args, **kwargs):
        start_time = time.time()

        try:
            response = await func(request, *args, **kwargs)
            REQUEST_COUNT.labels(
                method=request.method,
                endpoint=request.url.path,
                status=response.status_code
            ).inc()
            return response
        finally:
            REQUEST_DURATION.observe(time.time() - start_time)

    return wrapper
```

## 🔒 安全配置

### SSL/TLS 配置

```bash
# 生成自签名证书（开发环境）
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes

# 使用 Let's Encrypt（生产环境）
certbot certonly --nginx -d api.quantx.com
```

### 防火墙配置

```bash
# UFW 配置
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow 'Nginx Full'
sudo ufw allow from 10.0.0.0/8 to any port 5432  # 内网数据库访问
sudo ufw enable
```

### 应用安全

```python
# security/middleware.py
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
import time

class SecurityMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, rate_limit: int = 100):
        super().__init__(app)
        self.rate_limit = rate_limit
        self.requests = {}

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host

        # 速率限制
        current_time = time.time()
        if client_ip in self.requests:
            if current_time - self.requests[client_ip] < 60:  # 1分钟内
                raise HTTPException(status_code=429, detail="Rate limit exceeded")

        self.requests[client_ip] = current_time

        # 安全头
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"

        return response
```

## 🔄 故障恢复

### 自动故障转移

```bash
#!/bin/bash
# scripts/health_check.sh

SERVICE_URL="https://api.quantx.com/health"
BACKUP_SERVER="backup.quantx.com"

while true; do
    if ! curl -f --max-time 10 "$SERVICE_URL" > /dev/null 2>&1; then
        echo "⚠️ 主服务器异常，切换到备用服务器"

        # 更新 DNS 或负载均衡器配置
        # aws route53 change-resource-record-sets ...

        # 发送告警
        # curl -X POST "$SLACK_WEBHOOK" -d '{"text":"QuantX 主服务器故障"}'

        break
    fi

    sleep 30
done
```

### 数据恢复

```bash
#!/bin/bash
# scripts/restore_backup.sh

BACKUP_FILE="$1"
DATABASE="quantx"

if [ -z "$BACKUP_FILE" ]; then
    echo "用法: $0 <backup_file>"
    exit 1
fi

echo "🔄 开始数据恢复"

# 停止应用
sudo systemctl stop quantx

# 恢复数据库
gunzip -c "$BACKUP_FILE" | psql -U quantx -d "$DATABASE"

# 重启应用
sudo systemctl start quantx

# 验证恢复
sleep 10
curl -f http://localhost:8000/health

echo "✅ 数据恢复完成"
```

---

**相关文档**：
- [系统架构](./ARCHITECTURE.md)
- [故障排查](./TROUBLESHOOTING.md)
- [性能优化](./PERFORMANCE.md)
- [测试指南](./TESTING_GUIDE.md)

**有用的命令**：
```bash
# 查看服务状态
sudo systemctl status quantx

# 查看日志
tail -f /opt/quantx/logs/quantx.log

# 重启服务
sudo systemctl restart quantx

# 检查数据库连接
psql -h localhost -U quantx -d quantx -c "SELECT version();"

# 监控资源使用
htop
iotop
```

*请根据实际环境调整配置参数，确保生产环境的安全性和可靠性。*
