#!/bin/sh
set -u

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
IOS_ROOT=$(dirname "$SCRIPT_DIR")
REPO_ROOT=$(CDPATH= cd -- "$IOS_ROOT/../.." && pwd)
BACKEND_BASE_URL=${1:-${QUANTX_BACKEND_BASE_URL:-http://192.168.5.6:8080}}
BACKEND_BASE_URL=${BACKEND_BASE_URL%/}

if command -v curl >/dev/null 2>&1; then
  CURL=$(command -v curl)
elif [ -x /usr/bin/curl ]; then
  CURL=/usr/bin/curl
else
  CURL=
fi
if command -v lsof >/dev/null 2>&1; then
  LSOF=$(command -v lsof)
elif [ -x /usr/sbin/lsof ]; then
  LSOF=/usr/sbin/lsof
else
  LSOF=
fi
if command -v security >/dev/null 2>&1; then
  SECURITY=$(command -v security)
elif [ -x /usr/bin/security ]; then
  SECURITY=/usr/bin/security
else
  SECURITY=
fi

BLOCK_COUNT=0
WARN_COUNT=0
CODEGEN_READY=1
ACCOUNT_READY=1
TESTFLIGHT_READY=1

pass() {
  printf 'PASS  %s\n' "$1"
}

warn() {
  WARN_COUNT=$((WARN_COUNT + 1))
  printf 'WARN  %s\n' "$1"
}

block() {
  BLOCK_COUNT=$((BLOCK_COUNT + 1))
  printf 'BLOCK %s\n' "$1"
}

require_command() {
  if command -v "$1" >/dev/null 2>&1; then
    pass "命令可用：$1"
  else
    block "缺少命令：$1"
    return 1
  fi
}

setting_value() {
  printf '%s\n' "$1" |
    sed -n "s/^[[:space:]]*$2 = //p" |
    tail -n 1
}

case "$BACKEND_BASE_URL" in
  http://* | https://*) ;;
  *)
    printf 'Usage: %s [http[s]://backend-host:port]\n' "$0" >&2
    exit 64
    ;;
esac

BYPASS_BACKEND_PROXY=0
case "$BACKEND_BASE_URL" in
  http://127.* | https://127.* | \
    http://localhost* | https://localhost* | \
    http://10.* | https://10.* | \
    http://192.168.* | https://192.168.* | \
    http://172.1[6-9].* | https://172.1[6-9].* | \
    http://172.2[0-9].* | https://172.2[0-9].* | \
    http://172.3[01].* | https://172.3[01].*)
    BYPASS_BACKEND_PROXY=1
    ;;
esac

curl_backend() {
  if [ "$BYPASS_BACKEND_PROXY" -eq 1 ]; then
    "$CURL" --noproxy '*' "$@"
  else
    "$CURL" "$@"
  fi
}

printf 'QuantX iOS readiness check\n'
printf 'Backend: %s\n\n' "$BACKEND_BASE_URL"

require_command xcodebuild || TESTFLIGHT_READY=0
require_command xcrun || TESTFLIGHT_READY=0
if command -v xcodegen >/dev/null 2>&1; then
  pass "命令可用：xcodegen"
else
  block "缺少命令：xcodegen"
  CODEGEN_READY=0
fi
if command -v npm >/dev/null 2>&1; then
  pass "命令可用：npm"
else
  block "缺少命令：npm"
  CODEGEN_READY=0
fi
if command -v node >/dev/null 2>&1; then
  NODE=$(command -v node)
  pass "命令可用：node"
else
  NODE=
  block "缺少命令：node"
  ACCOUNT_READY=0
fi
if [ -n "$CURL" ]; then
  pass "命令可用：curl"
else
  block "缺少命令：curl"
  ACCOUNT_READY=0
fi

PUBLISHED_SCHEMA="$REPO_ROOT/apps/docs/public/contracts/graphql-schema.graphql"
CODEGEN_CONFIG="$IOS_ROOT/apollo-codegen-config.json"

if [ -f "$PUBLISHED_SCHEMA" ]; then
  if grep -Eq 'accountId: String!? = "[0-9]{8,}"' \
    "$PUBLISHED_SCHEMA"
  then
    block "发布 schema 含账号型默认值，必须先修复契约"
    CODEGEN_READY=0
  else
    pass "发布 schema 存在且未发现账号型默认值"
  fi
else
  block "发布 schema 缺失：apps/docs/public/contracts/graphql-schema.graphql"
  CODEGEN_READY=0
fi

if grep -Fq '../docs/public/contracts/graphql-schema.graphql' "$CODEGEN_CONFIG"; then
  pass "Apollo iOS 直接读取 apps/docs 发布契约"
else
  block "Apollo iOS codegen 未指向 apps/docs 发布契约"
  CODEGEN_READY=0
fi

if [ -f "$IOS_ROOT/QuantX/GraphQL/Generated/Schema/SchemaMetadata.graphql.swift" ]; then
  pass "Apollo iOS 生成类型存在"
else
  warn "Apollo iOS 生成类型尚不存在，请运行 apps/ios/scripts/codegen.sh"
fi

if [ -n "$LSOF" ]; then
  if "$LSOF" -nP -iTCP:8080 -sTCP:LISTEN >/dev/null 2>&1; then
    warn "本机 8080 已被占用；执行 codegen 前必须只停止该监听进程并重启后端"
  else
    pass "本机 8080 当前可用于固定端口后端"
  fi
else
  warn "无法检查本机 8080 监听状态"
fi

if [ -x "$IOS_ROOT/apollo-ios-cli" ]; then
  pass "Apollo CLI 已安装"
else
  warn "Apollo CLI 尚未安装；codegen 时会由安装脚本获取"
fi

DEBUG_CONFIG="$IOS_ROOT/Config/Debug.xcconfig"
STAGING_CONFIG="$IOS_ROOT/Config/Staging.xcconfig"
RELEASE_CONFIG="$IOS_ROOT/Config/Release.xcconfig"
EFFECTIVE_CONFIG_READY=0
STAGING_ACCOUNT_ENABLED=NO
STAGING_AUTH_BASE_URL=
STAGING_TEAM=

if command -v xcodebuild >/dev/null 2>&1 && [ -d "$IOS_ROOT/QuantX.xcodeproj" ] && \
  DEBUG_SETTINGS=$(
    xcodebuild -project "$IOS_ROOT/QuantX.xcodeproj" -scheme QuantX \
      -configuration Debug -showBuildSettings 2>/dev/null
  ) && \
  STAGING_SETTINGS=$(
    xcodebuild -project "$IOS_ROOT/QuantX.xcodeproj" -scheme QuantX \
      -configuration Staging -showBuildSettings 2>/dev/null
  ) && \
  RELEASE_SETTINGS=$(
    xcodebuild -project "$IOS_ROOT/QuantX.xcodeproj" -scheme QuantX \
      -configuration Release -showBuildSettings 2>/dev/null
  )
then
  EFFECTIVE_CONFIG_READY=1
  DEBUG_ACCOUNT_ENABLED=$(setting_value "$DEBUG_SETTINGS" QUANTX_ACCOUNT_DATA_ENABLED)
  STAGING_ACCOUNT_ENABLED=$(setting_value "$STAGING_SETTINGS" QUANTX_ACCOUNT_DATA_ENABLED)
  STAGING_HTTP_URL=$(setting_value "$STAGING_SETTINGS" QUANTX_GRAPHQL_HTTP_URL)
  STAGING_WS_URL=$(setting_value "$STAGING_SETTINGS" QUANTX_GRAPHQL_WEBSOCKET_URL)
  STAGING_AUTH_BASE_URL=$(setting_value "$STAGING_SETTINGS" QUANTX_AUTH_BASE_URL)
  RELEASE_HTTP_URL=$(setting_value "$RELEASE_SETTINGS" QUANTX_GRAPHQL_HTTP_URL)
  RELEASE_WS_URL=$(setting_value "$RELEASE_SETTINGS" QUANTX_GRAPHQL_WEBSOCKET_URL)
  RELEASE_AUTH_BASE_URL=$(setting_value "$RELEASE_SETTINGS" QUANTX_AUTH_BASE_URL)
  STAGING_TEAM=$(setting_value "$STAGING_SETTINGS" DEVELOPMENT_TEAM)

  if [ "$DEBUG_ACCOUNT_ENABLED" = YES ]; then
    pass "Debug 有效构建设置已启用真实账户数据"
  else
    block "Debug 有效构建设置未启用真实账户数据"
    ACCOUNT_READY=0
  fi

  case "$STAGING_HTTP_URL $STAGING_WS_URL $STAGING_AUTH_BASE_URL" in
    *".invalid"* | *"replace-with"* | "  ")
      block "Staging 有效构建设置仍使用占位地址"
      TESTFLIGHT_READY=0
      ;;
    *)
      case "$STAGING_HTTP_URL|$STAGING_WS_URL|$STAGING_AUTH_BASE_URL" in
        https://*\|wss://*\|https://*)
          pass "Staging 有效构建设置使用 HTTPS/WSS 非占位地址"
          ;;
        *)
          block "Staging 有效构建设置未全部使用 HTTPS/WSS"
          TESTFLIGHT_READY=0
          ;;
      esac
      ;;
  esac

  case "$RELEASE_HTTP_URL $RELEASE_WS_URL $RELEASE_AUTH_BASE_URL" in
    *".invalid"* | *"replace-with"* | "  ")
      block "Release 有效构建设置仍使用占位地址"
      TESTFLIGHT_READY=0
      ;;
    *)
      case "$RELEASE_HTTP_URL|$RELEASE_WS_URL|$RELEASE_AUTH_BASE_URL" in
        https://*\|wss://*\|https://*)
          pass "Release 有效构建设置使用 HTTPS/WSS 非占位地址"
          ;;
        *)
          block "Release 有效构建设置未全部使用 HTTPS/WSS"
          TESTFLIGHT_READY=0
          ;;
      esac
      ;;
  esac
else
  block "无法读取 Xcode 有效构建设置；请先运行 xcodegen generate"
  TESTFLIGHT_READY=0
fi

if grep -q 'DEBUG_HTTP_ALLOWED = 1' "$DEBUG_CONFIG" && \
  grep -q 'DEBUG_HTTP_ALLOWED = 0' "$STAGING_CONFIG" && \
  grep -q 'DEBUG_HTTP_ALLOWED = 0' "$RELEASE_CONFIG"
then
  pass "明文 ATS 例外仅限 Debug"
else
  block "ATS Debug/Release 边界配置不符合预期"
  TESTFLIGHT_READY=0
fi

if grep -q 'QUANTX_ACCOUNT_DATA_ENABLED = NO' "$STAGING_CONFIG" && \
  grep -q 'QUANTX_ACCOUNT_DATA_ENABLED = NO' "$RELEASE_CONFIG"
then
  pass "Staging/Release 账户数据默认关闭"
else
  block "Staging/Release 账户数据未默认关闭"
  TESTFLIGHT_READY=0
fi

case "$BACKEND_BASE_URL" in
  https://*) pass "真实账户候选入口使用 HTTPS" ;;
  http://*)
    if [ "$BYPASS_BACKEND_PROXY" -eq 1 ]; then
      pass "Debug 允许通过私网 HTTP/WS 联调真实账户数据"
      TESTFLIGHT_READY=0
    else
      block "公网明文 HTTP 不允许承载账户数据"
      ACCOUNT_READY=0
      TESTFLIGHT_READY=0
    fi
    ;;
esac

if [ -n "$CURL" ]; then
  TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/quantx-ios-readiness.XXXXXX")
  HEALTH_BODY="$TEMP_DIR/health.json"
  AUTH_BODY="$TEMP_DIR/auth.json"
  GRAPHQL_BODY="$TEMP_DIR/graphql.json"
  OPENAPI_BODY="$TEMP_DIR/openapi.json"
  cleanup() {
    rm -f "$HEALTH_BODY" "$AUTH_BODY" "$GRAPHQL_BODY" "$OPENAPI_BODY"
    rmdir "$TEMP_DIR" 2>/dev/null || true
  }
  trap cleanup EXIT INT TERM

  if HEALTH_STATUS=$(
    curl_backend --silent --show-error --max-time 5 \
      --output "$HEALTH_BODY" --write-out '%{http_code}' \
      "$BACKEND_BASE_URL/health"
  ); then
    if [ "$HEALTH_STATUS" = 200 ] && \
      grep -Eq '"status"[[:space:]]*:[[:space:]]*"(healthy|ready)"' "$HEALTH_BODY"
    then
      pass "真实后端健康检查通过"
    else
      block "真实后端健康检查未通过（HTTP ${HEALTH_STATUS}）"
      ACCOUNT_READY=0
    fi
  else
    block "无法连接真实后端健康检查"
    ACCOUNT_READY=0
  fi

  if AUTH_STATUS=$(
    curl_backend --silent --show-error --max-time 5 \
      --output "$AUTH_BODY" --write-out '%{http_code}' \
      "$BACKEND_BASE_URL/auth/session"
  ); then
    case "$AUTH_STATUS" in
      401 | 403)
        if grep -Eq '"code"[[:space:]]*:[[:space:]]*"(UNAUTHENTICATED|FORBIDDEN)"' \
          "$AUTH_BODY" && \
          grep -Eq '"message"[[:space:]]*:' "$AUTH_BODY" && \
          grep -Eq '"requestId"[[:space:]]*:' "$AUTH_BODY" && \
          grep -Eq '"retryable"[[:space:]]*:' "$AUTH_BODY"
        then
          pass "真实后端认证路由默认拒绝且错误信封完整（HTTP ${AUTH_STATUS}）"
        else
          block "真实后端认证错误信封不符合 iOS 契约（HTTP ${AUTH_STATUS}）"
          ACCOUNT_READY=0
        fi
        ;;
      200)
        block "真实后端无凭证会话请求被接受"
        ACCOUNT_READY=0
        ;;
      404)
        block "真实后端尚未部署 /auth/session"
        ACCOUNT_READY=0
        ;;
      *)
        block "无法证明真实后端已部署认证路由（HTTP ${AUTH_STATUS}）"
        ACCOUNT_READY=0
        ;;
    esac
  else
    block "无法探测真实后端认证路由"
    ACCOUNT_READY=0
  fi

  if OPENAPI_STATUS=$(
    curl_backend --silent --show-error --max-time 5 \
      --output "$OPENAPI_BODY" --write-out '%{http_code}' \
      "$BACKEND_BASE_URL/docs/contracts/openapi-client.json"
  ); then
    OPENAPI_CONTRACT_READY=1
    if [ "$OPENAPI_STATUS" != 200 ]; then
      OPENAPI_CONTRACT_READY=0
    else
      for contract_marker in \
        '"/auth/session"' \
        '"/auth/session/refresh"' \
        '"LoginRequest"' \
        '"RefreshRequest"' \
        '"SessionGrantResponse"' \
        '"SessionStateResponse"' \
        '"deviceSessionId"' \
        '"authorizedAccountIds"'
      do
        if ! grep -Fq "$contract_marker" "$OPENAPI_BODY"; then
          OPENAPI_CONTRACT_READY=0
        fi
      done
    fi

    if [ "$OPENAPI_CONTRACT_READY" -eq 1 ]; then
      pass "真实后端认证 OpenAPI 契约与 iOS 字段要求一致"
    else
      block "无法验证真实后端认证 OpenAPI 契约（HTTP ${OPENAPI_STATUS}）"
      ACCOUNT_READY=0
    fi
  else
    block "无法读取真实后端认证 OpenAPI 契约"
    ACCOUNT_READY=0
  fi

  if GRAPHQL_STATUS=$(
    curl_backend --silent --show-error --max-time 5 \
      --header 'Content-Type: application/json' \
      --data '{"query":"query IOSReadinessProbe { strategyInstances { id } }"}' \
      --output "$GRAPHQL_BODY" --write-out '%{http_code}' \
      "$BACKEND_BASE_URL/graphql"
  ); then
    if grep -Eq '"data"[[:space:]]*:[[:space:]]*\{[[:space:]]*"strategyInstances"' \
      "$GRAPHQL_BODY"
    then
      block "匿名 GraphQL 查询仍被接受（HTTP ${GRAPHQL_STATUS}）"
      ACCOUNT_READY=0
    elif [ "$GRAPHQL_STATUS" = 401 ] || \
      grep -Eq 'UNAUTHENTICATED|FORBIDDEN' "$GRAPHQL_BODY"
    then
      pass "匿名 GraphQL 查询已被拒绝"
    else
      block "无法证明匿名 GraphQL 已默认拒绝（HTTP ${GRAPHQL_STATUS}）"
      ACCOUNT_READY=0
    fi
  else
    block "无法探测真实后端 GraphQL 安全边界"
    ACCOUNT_READY=0
  fi

  case "$BACKEND_BASE_URL" in
    https://*)
      GRAPHQL_WS_URL="wss://${BACKEND_BASE_URL#https://}/graphql"
      ;;
    *)
      GRAPHQL_WS_URL="ws://${BACKEND_BASE_URL#http://}/graphql"
      ;;
  esac
  if [ -n "$NODE" ]; then
    if "$NODE" "$SCRIPT_DIR/probe-graphql-ws.mjs" "$GRAPHQL_WS_URL" \
      >/dev/null 2>&1
    then
      pass "匿名 GraphQL WebSocket 连接已被拒绝"
    else
      block "无法证明匿名 GraphQL WebSocket 已默认拒绝"
      ACCOUNT_READY=0
    fi
  fi
fi

if [ -n "$SECURITY" ]; then
  IDENTITY_COUNT=$(
    "$SECURITY" find-identity -v -p codesigning 2>/dev/null |
      sed -n 's/.*\([0-9][0-9]*\) valid identities found.*/\1/p' |
      tail -n 1
  )
  if [ -n "$IDENTITY_COUNT" ] && [ "$IDENTITY_COUNT" -gt 0 ]; then
    pass "本机存在有效代码签名身份（数量：${IDENTITY_COUNT}）"
  else
    warn "本机未发现有效代码签名身份"
    TESTFLIGHT_READY=0
  fi
else
  warn "无法检查本机代码签名身份"
  TESTFLIGHT_READY=0
fi

case "$STAGING_TEAM" in
  "" | *REPLACE* | *YOUR_TEAM*)
    warn "Staging 有效构建设置尚未配置 Development Team"
    TESTFLIGHT_READY=0
    ;;
  *)
    pass "Staging 有效构建设置已配置 Development Team"
    ;;
esac

if [ "$EFFECTIVE_CONFIG_READY" -eq 1 ] && [ "$STAGING_ACCOUNT_ENABLED" = YES ]; then
  if [ "$STAGING_AUTH_BASE_URL" = "$BACKEND_BASE_URL" ]; then
    pass "Staging 账户连接已指向本次验证的后端"
  else
    block "Staging 认证地址与本次验证后端不一致"
    ACCOUNT_READY=0
    TESTFLIGHT_READY=0
  fi
else
  warn "Staging 账户数据开关尚未启用"
  ACCOUNT_READY=0
  TESTFLIGHT_READY=0
fi

if [ "$ACCOUNT_READY" -ne 1 ]; then
  TESTFLIGHT_READY=0
fi

printf '\nSUMMARY codegen=%s account_data=%s testflight=%s blocks=%s warnings=%s\n' \
  "$( [ "$CODEGEN_READY" -eq 1 ] && printf READY || printf BLOCKED )" \
  "$( [ "$ACCOUNT_READY" -eq 1 ] && printf READY || printf BLOCKED )" \
  "$( [ "$TESTFLIGHT_READY" -eq 1 ] && printf READY || printf BLOCKED )" \
  "$BLOCK_COUNT" \
  "$WARN_COUNT"

if [ "$BLOCK_COUNT" -gt 0 ] || \
  [ "$CODEGEN_READY" -ne 1 ] || \
  [ "$ACCOUNT_READY" -ne 1 ] || \
  [ "$TESTFLIGHT_READY" -ne 1 ]
then
  exit 2
fi
