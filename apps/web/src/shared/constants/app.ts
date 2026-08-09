// 应用相关常量

export const APP_CONFIG = {
  NAME: 'QuantX',
  VERSION: '1.0.0',
  DESCRIPTION: '量化交易平台',
} as const;

export const STORAGE_KEYS = {
  USER_PREFERENCES: 'quantx_user_preferences',
  THEME: 'quantx_theme',
  LANGUAGE: 'quantx_language',
  CHART_SETTINGS: 'quantx_chart_settings',
  WATCHLIST: 'quantx_watchlist',
} as const;

export const THEMES = {
  LIGHT: 'light',
  DARK: 'dark',
  SYSTEM: 'system',
} as const;

export const LANGUAGES = {
  ZH_CN: 'zh-CN',
  EN_US: 'en-US',
} as const;

export const PAGE_SIZES = [10, 20, 50, 100] as const;

export const CHART_INTERVALS = {
  '1m': '1分钟',
  '5m': '5分钟',
  '15m': '15分钟',
  '30m': '30分钟',
  '1h': '1小时',
  '4h': '4小时',
  '1d': '日线',
  '1w': '周线',
  '1M': '月线',
} as const;

export const TIME_RANGES = {
  '1D': '今日',
  '1W': '1周',
  '1M': '1个月',
  '3M': '3个月',
  '6M': '6个月',
  '1Y': '1年',
  YTD: '年初至今',
  ALL: '全部',
} as const;

export const REFRESH_INTERVALS = {
  REAL_TIME: 1000, // 1秒
  FAST: 5000, // 5秒
  NORMAL: 10000, // 10秒
  SLOW: 30000, // 30秒
  MANUAL: 0, // 手动刷新
} as const;
