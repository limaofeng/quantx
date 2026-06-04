import {
  ColorType,
  type DeepPartial,
  type ChartOptions,
} from 'lightweight-charts';

import { formatIntradayTick, formatTime, formatDate } from './time-utils';

export const getCommonOptions = (
  isTimeMode: boolean
): DeepPartial<ChartOptions> => ({
  layout: {
    background: { type: ColorType.Solid, color: 'transparent' },
    textColor: '#71717a',
    fontFamily: "Inter, 'Microsoft YaHei', sans-serif",
  },
  grid: {
    vertLines: { color: 'rgba(39, 39, 42, 0.1)' },
    horzLines: { color: 'rgba(39, 39, 42, 0.1)' },
  },
  localization: {
    timeFormatter: isTimeMode ? formatTime : formatDate,
  },
  handleScroll: !isTimeMode,
  handleScale: !isTimeMode,
  timeScale: {
    borderColor: 'rgba(39, 39, 42, 0.2)',
    timeVisible: true,
    secondsVisible: false,

    fixLeftEdge: isTimeMode,
    fixRightEdge: true,
    tickMarkFormatter: isTimeMode ? formatIntradayTick : formatDate,
  },
  crosshair: {
    vertLine: {
      labelVisible: true,
      labelBackgroundColor: '#2563eb',
      style: 3,
      color: 'rgba(255, 255, 255, 0.4)',
    },
    horzLine: {
      labelVisible: true,
      labelBackgroundColor: '#2563eb',
      style: 3,
      color: 'rgba(255, 255, 255, 0.4)',
    },
  },
});

export const PERIODS = [
  { label: '五日', value: '5d' },
  { label: '日K', value: '1d' },
  { label: '周K', value: '1w' },
  { label: '月K', value: '1M' },
];

export const MINUTE_PERIODS = [
  { label: '1分', value: '1m' },
  { label: '5分', value: '5m' },
  { label: '15分', value: '15m' },
  { label: '30分', value: '30m' },
  { label: '60分', value: '60m' },
  { label: '120分', value: '120m' },
];
