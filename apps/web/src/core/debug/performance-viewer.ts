/**
 * 性能监控调试工具
 * 开发环境下可以通过浏览器控制台查看性能数据
 */
/* eslint-disable no-console */

import {
  clearGraphqlRequestTimings,
  getGraphqlRequestTimings,
} from '@/core/performance/graphql-timing';
import {
  webVitals,
  performanceBudget,
  type PerformanceMetric,
} from '@/core/performance/web-vitals';

interface DebugPerformanceTools {
  // 查看性能摘要
  summary: () => void;
  // 查看所有性能指标
  metrics: () => void;
  // 查看特定指标
  metric: (name: string) => void;
  // 查看性能预算
  budget: () => void;
  // 设置性能预算
  setBudget: (metric: string, threshold: number) => void;
  // 检查预算违规
  violations: () => void;
  // 实时监控
  monitor: (enable?: boolean) => void;
  // 查看 GraphQL 请求级耗时
  graphql: () => void;
  // 查看某次 GraphQL 请求的字段和 SQL 明细
  graphqlFields: (requestId?: string) => void;
  // 清空 GraphQL 请求耗时
  clearGraphql: () => void;
}

declare global {
  interface Window {
    debugPerformance?: DebugPerformanceTools;
  }
}

// 创建性能调试工具
const createDebugPerformanceTools = (): DebugPerformanceTools => {
  let monitoringEnabled = false;
  let unsubscribe: (() => void) | null = null;

  return {
    summary: () => {
      const summary = webVitals.getSummary();
      console.group('📊 性能摘要');
      console.log(`总指标数: ${summary.total}`);
      console.log(
        `良好: ${summary.good} (${((summary.good / summary.total) * 100).toFixed(1)}%)`
      );
      console.log(
        `需改进: ${summary.needsImprovement} (${((summary.needsImprovement / summary.total) * 100).toFixed(1)}%)`
      );
      console.log(
        `较差: ${summary.poor} (${((summary.poor / summary.total) * 100).toFixed(1)}%)`
      );

      if (Object.keys(summary.metrics).length > 0) {
        console.log('\n最新指标:');
        console.table(
          Object.entries(summary.metrics).map(([name, metric]) => ({
            指标: name,
            值: metric.value.toFixed(2),
            评级: metric.rating,
            单位: getMetricUnit(name),
          }))
        );
      }
      console.groupEnd();
    },

    metrics: () => {
      const metrics = webVitals.getMetrics();
      console.group('📈 所有性能指标');
      if (metrics.length === 0) {
        console.log('暂无性能数据');
      } else {
        console.table(
          metrics.map(metric => ({
            时间: new Date(metric.timestamp).toLocaleString(),
            指标: metric.name,
            值: metric.value.toFixed(2),
            评级: metric.rating,
            变化: metric.delta.toFixed(2),
            URL: metric.url.split('/').pop() || '根页面',
          }))
        );
      }
      console.groupEnd();
    },

    metric: (name: string) => {
      const metric = webVitals.getMetric(name);
      console.group(`🎯 ${name} 指标详情`);
      if (!metric) {
        console.log(`未找到 ${name} 指标数据`);
      } else {
        console.log('指标名称:', metric.name);
        console.log('当前值:', metric.value.toFixed(2), getMetricUnit(name));
        console.log(
          '评级:',
          getMetricRatingEmoji(metric.rating),
          metric.rating
        );
        console.log('变化量:', metric.delta.toFixed(2));
        console.log('时间戳:', new Date(metric.timestamp).toLocaleString());
        console.log('页面URL:', metric.url);
        console.log('性能条目:', metric.entries);
      }
      console.groupEnd();
    },

    budget: () => {
      const violations = performanceBudget.getBudgetViolations();
      console.group('💰 性能预算状态');
      if (violations.length === 0) {
        console.log('✅ 所有指标都在预算范围内');
      } else {
        console.log('❌ 发现预算违规:');
        console.table(
          violations.map(violation => ({
            指标: violation.metric.name,
            当前值: violation.metric.value.toFixed(2),
            预算: violation.budget.toFixed(2),
            超出: violation.overage.toFixed(2),
            单位: getMetricUnit(violation.metric.name),
          }))
        );
      }
      console.groupEnd();
    },

    setBudget: (metric: string, threshold: number) => {
      performanceBudget.setBudget(metric, threshold);
      console.log(
        `✅ 已设置 ${metric} 性能预算为 ${threshold} ${getMetricUnit(metric)}`
      );
    },

    violations: () => {
      const violations = performanceBudget.getBudgetViolations();
      console.group('⚠️ 预算违规详情');
      if (violations.length === 0) {
        console.log('没有预算违规');
      } else {
        violations.forEach(violation => {
          console.log(
            `${getMetricRatingEmoji('poor')} ${violation.metric.name}: ${violation.metric.value.toFixed(2)} > ${violation.budget.toFixed(2)} (超出 ${violation.overage.toFixed(2)} ${getMetricUnit(violation.metric.name)})`
          );
        });
      }
      console.groupEnd();
    },

    monitor: (enable = true) => {
      if (enable && !monitoringEnabled) {
        monitoringEnabled = true;
        unsubscribe = webVitals.addListener((metric: PerformanceMetric) => {
          const emoji = getMetricRatingEmoji(metric.rating);
          console.log(
            `${emoji} Web Vital: ${metric.name} = ${metric.value.toFixed(2)} ${getMetricUnit(metric.name)} (${metric.rating})`
          );
        });
        console.log('🔄 性能实时监控已启用');
      } else if (!enable && monitoringEnabled) {
        monitoringEnabled = false;
        if (unsubscribe) {
          unsubscribe();
          unsubscribe = null;
        }
        console.log('⏹️ 性能实时监控已停用');
      } else {
        console.log('监控状态:', monitoringEnabled ? '已启用' : '已停用');
      }
    },

    graphql: () => {
      const timings = getGraphqlRequestTimings();
      console.group('🔎 GraphQL 请求耗时');
      if (timings.length === 0) {
        console.log('暂无 GraphQL 网络请求数据');
      } else {
        console.table(
          [...timings].reverse().map(timing => {
            const slowestField = timing.server?.fields[0];
            return {
              时间: new Date(timing.receivedAt).toLocaleTimeString(),
              操作: timing.operationName,
              类型: timing.operationType,
              客户端毫秒: timing.clientMs.toFixed(1),
              GraphQL毫秒:
                timing.graphqlMs === null ? '-' : timing.graphqlMs.toFixed(1),
              外围毫秒:
                timing.outsideGraphqlMs === null
                  ? '-'
                  : timing.outsideGraphqlMs.toFixed(1),
              SQL毫秒: timing.server?.sql.totalMs.toFixed(1) ?? '-',
              SQL次数: timing.server?.sql.count ?? '-',
              最慢字段: slowestField
                ? `${slowestField.parentType}.${slowestField.field} ${slowestField.totalMs.toFixed(1)}ms`
                : '-',
              错误: timing.hasError ? '是' : '否',
              请求ID: timing.requestId,
              路由: timing.route,
            };
          })
        );
      }
      console.groupEnd();
    },

    graphqlFields: (requestId?: string) => {
      const timings = getGraphqlRequestTimings();
      const timing = requestId
        ? [...timings].reverse().find(item => item.requestId === requestId)
        : timings[timings.length - 1];
      console.group(`🧩 GraphQL 字段耗时 ${requestId ?? '(最近一次)'}`);
      if (!timing) {
        console.log('未找到对应 GraphQL 请求');
      } else if (!timing.server) {
        console.log('该请求没有服务端字段明细；请确认当前为开发环境');
      } else {
        console.log({
          requestId: timing.requestId,
          operation: timing.operationName,
          route: timing.route,
          clientMs: timing.clientMs,
          graphqlMs: timing.graphqlMs,
          phases: timing.server.phases,
          fieldInvocations: timing.server.fieldInvocations,
          fieldsTruncated: timing.server.fieldsTruncated,
          sql: timing.server.sql,
        });
        console.table(
          timing.server.fields.map(field => ({
            字段: `${field.parentType}.${field.field}`,
            路径: field.paths.join(', '),
            次数: field.count,
            总毫秒: field.totalMs.toFixed(3),
            最大毫秒: field.maxMs.toFixed(3),
            错误: field.errors,
          }))
        );
        if (timing.server.sql.statements.length > 0) {
          console.table(
            timing.server.sql.statements.map(statement => ({
              类型: statement.kind,
              指纹: statement.fingerprint,
              次数: statement.count,
              总毫秒: statement.totalMs.toFixed(3),
              最大毫秒: statement.maxMs.toFixed(3),
              错误: statement.errors,
            }))
          );
        }
      }
      console.groupEnd();
    },

    clearGraphql: () => {
      clearGraphqlRequestTimings();
      console.log('GraphQL 请求耗时已清空');
    },
  };
};

// 辅助函数
function getMetricUnit(name: string): string {
  switch (name) {
    case 'CLS':
      return '(累积分数)';
    case 'FCP':
    case 'INP':
    case 'LCP':
    case 'TTFB':
      return 'ms';
    default:
      return '';
  }
}

function getMetricRatingEmoji(rating: string): string {
  switch (rating) {
    case 'good':
      return '✅';
    case 'needs-improvement':
      return '⚠️';
    case 'poor':
      return '❌';
    default:
      return 'ℹ️';
  }
}

// 在开发环境下将性能调试工具挂载到全局对象
console.log('性能调试工具环境检测:', {
  VITE_DEV: import.meta.env.DEV,
  VITE_PROD: import.meta.env.PROD,
  VITE_APP_ENV: import.meta.env.VITE_APP_ENV,
});

if (import.meta.env.DEV) {
  // 挂载到全局
  window.debugPerformance = createDebugPerformanceTools();

  // 输出使用提示
  console.log(
    '%c🚀 QuantX Performance Tools',
    'color: #f59e0b; font-size: 14px; font-weight: bold;'
  );
  console.log(
    '%c在控制台中使用以下命令查看性能数据:',
    'color: #6b7280; font-size: 12px;'
  );
  console.log(
    '%cdebugPerformance.summary()%c - 查看性能摘要',
    'color: #3b82f6; font-family: monospace;',
    'color: #6b7280;'
  );
  console.log(
    '%cdebugPerformance.metrics()%c - 查看所有指标',
    'color: #3b82f6; font-family: monospace;',
    'color: #6b7280;'
  );
  console.log(
    '%cdebugPerformance.metric("LCP")%c - 查看特定指标',
    'color: #3b82f6; font-family: monospace;',
    'color: #6b7280;'
  );
  console.log(
    '%cdebugPerformance.budget()%c - 查看性能预算',
    'color: #3b82f6; font-family: monospace;',
    'color: #6b7280;'
  );
  console.log(
    '%cdebugPerformance.setBudget("LCP", 2500)%c - 设置预算',
    'color: #3b82f6; font-family: monospace;',
    'color: #6b7280;'
  );
  console.log(
    '%cdebugPerformance.monitor()%c - 启用实时监控',
    'color: #3b82f6; font-family: monospace;',
    'color: #6b7280;'
  );
  console.log(
    '%cdebugPerformance.graphql()%c - 查看 GraphQL 请求耗时',
    'color: #3b82f6; font-family: monospace;',
    'color: #6b7280;'
  );
  console.log(
    '%cdebugPerformance.graphqlFields()%c - 查看最近请求的字段/SQL 明细',
    'color: #3b82f6; font-family: monospace;',
    'color: #6b7280;'
  );
}

export { createDebugPerformanceTools };
export type { DebugPerformanceTools };
