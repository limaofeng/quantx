/**
 * 表现追踪 Hook
 * 追踪筛选结果的表现
 */
export function useScreeningPerformance() {
  // TODO: 将来接入 GraphQL 查询
  // 目前这个 Tab 的内容由 PerformanceTracking 组件自己管理
  // 这个 Hook 可以在将来需要时扩展

  return {
    isLoading: false,
  };
}
