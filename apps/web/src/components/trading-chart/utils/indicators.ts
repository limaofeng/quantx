import {
  type CandlestickData,
  type HistogramData,
  type LineData,
} from 'lightweight-charts';

export type MainIndicatorType = 'MA' | 'EMA' | 'BOLL' | 'SAR';
export type SubIndicatorType = 'VOL' | 'MACD' | 'KDJ' | 'RSI';

export interface IndicatorData {
  name: string;
  data: (LineData | HistogramData)[];
  color?: string;
  options?: Record<string, unknown>;
  // Helper for series type distinction
  type: 'Line' | 'Histogram';
}

function getClose(data: CandlestickData): number {
  return data.close;
}

export function calculateSMA(
  data: CandlestickData[],
  period: number
): LineData[] {
  const result: LineData[] = [];
  for (let i = 0; i < data.length; i++) {
    const time = data[i].time;
    if (i < period - 1) {
      // Not enough data
      continue;
    }
    let sum = 0;
    for (let j = 0; j < period; j++) {
      sum += getClose(data[i - j]);
    }
    result.push({ time, value: sum / period });
  }
  return result;
}

export function calculateEMA(
  data: CandlestickData[],
  period: number
): LineData[] {
  const result: LineData[] = [];
  const k = 2 / (period + 1);
  let ema = getClose(data[0]);

  // Initial SMA for the first point (or just use first close)
  // To be simpler, we start from index 0 but real EMA needs warmup.
  // Let's just use close for i=0
  result.push({ time: data[0].time, value: ema });

  for (let i = 1; i < data.length; i++) {
    const time = data[i].time;
    const close = getClose(data[i]);
    ema = close * k + ema * (1 - k);
    result.push({ time, value: ema });
  }
  return result;
}

// BOLL: MB (Middle Band) = MA20, UP = MB + 2*STD, DN = MB - 2*STD
export function calculateBOLL(
  data: CandlestickData[],
  period: number = 20,
  multiplier: number = 2
): {
  upper: LineData[];
  middle: LineData[];
  lower: LineData[];
} {
  const upper: LineData[] = [];
  const middle: LineData[] = [];
  const lower: LineData[] = [];

  for (let i = 0; i < data.length; i++) {
    const time = data[i].time;
    if (i < period - 1) continue;

    let sum = 0;
    for (let j = 0; j < period; j++) {
      sum += getClose(data[i - j]);
    }
    const ma = sum / period;

    let sumSqDiff = 0;
    for (let j = 0; j < period; j++) {
      const diff = getClose(data[i - j]) - ma;
      sumSqDiff += diff * diff;
    }
    const std = Math.sqrt(sumSqDiff / period);

    middle.push({ time, value: ma });
    upper.push({ time, value: ma + std * multiplier });
    lower.push({ time, value: ma - std * multiplier });
  }

  return { upper, middle, lower };
}

// SAR is complex to implement correctly from scratch, often recursive.
// I'll skip complex implementation and return a simplified trend follower or mock.
// Or just use a simple mock for now as requested.
export function calculateSAR(data: CandlestickData[]): LineData[] {
  // Mock SAR: just below Low in uptrend, above High in downtrend.
  // Very simplified logic.
  const result: LineData[] = [];
  let isUp = true;
  let af = 0.02;
  let ep = data[0].high;
  let sar = data[0].low;

  for (let i = 1; i < data.length; i++) {
    const time = data[i].time;
    // Mock logic: switch trend if Close crosses SAR
    // This is not real SAR formula but enough for visualization
    if (isUp) {
      sar = sar + af * (ep - sar);
      if (data[i].low < sar) {
        isUp = false;
        sar = data[i].high;
        ep = data[i].low;
        af = 0.02;
      } else {
        if (data[i].high > ep) {
          ep = data[i].high;
          af = Math.min(af + 0.02, 0.2);
        }
      }
    } else {
      sar = sar - af * (sar - ep);
      if (data[i].high > sar) {
        isUp = true;
        sar = data[i].low;
        ep = data[i].high;
        af = 0.02;
      } else {
        if (data[i].low < ep) {
          ep = data[i].low;
          af = Math.min(af + 0.02, 0.2);
        }
      }
    }
    result.push({ time, value: sar });
  }
  return result;
}

// MACD: DIF = EMA(12) - EMA(26). DEA = EMA(DIF, 9). MACD = 2 * (DIF - DEA)
export function calculateMACD(
  data: CandlestickData[],
  fast: number = 12,
  slow: number = 26,
  signal: number = 9
): {
  diff: LineData[];
  dea: LineData[];
  macd: HistogramData[];
} {
  const emaFast = calculateEMA(data, fast); // Note: aligned to start from index 0
  const emaSlow = calculateEMA(data, slow);

  const diffData: LineData[] = [];

  // EmaSlow is shorter or same length depending on implementation, but here both return full length (with warmup)
  // But calculateEMA above returns full length from index 0.

  for (let i = 0; i < data.length; i++) {
    const dif = emaFast[i].value - emaSlow[i].value;
    diffData.push({ time: data[i].time, value: dif });
  }

  // DEA is EMA of DIF
  // We need to implement EMA for LineData input (value instead of close)
  const deaData: LineData[] = [];
  const k = 2 / (signal + 1);
  let ema = diffData[0].value;
  deaData.push({ time: diffData[0].time, value: ema });

  for (let i = 1; i < diffData.length; i++) {
    const val = diffData[i].value;
    ema = val * k + ema * (1 - k);
    deaData.push({ time: diffData[i].time, value: ema });
  }

  const macdData: HistogramData[] = [];
  for (let i = 0; i < diffData.length; i++) {
    const val = 2 * (diffData[i].value - deaData[i].value);
    const color = val >= 0 ? '#ef4444' : '#22c55e'; // Red for up (MACD usually follows price action color conventions in China: Red=Up)
    // Wait, standard MACD: positive is usually bullish (Red in CN), negative bearish (Green in CN).
    macdData.push({ time: diffData[i].time, value: val, color });
  }

  return { diff: diffData, dea: deaData, macd: macdData };
}

// KDJ Mock
// RSV = (Close - LowestLow9) / (HighestHigh9 - LowestLow9) * 100
// K = 2/3 * PrevK + 1/3 * RSV
// D = 2/3 * PrevD + 1/3 * K
// J = 3K - 2D
export function calculateKDJ(
  data: CandlestickData[],
  n: number = 9
): {
  k: LineData[];
  d: LineData[];
  j: LineData[];
} {
  const kData: LineData[] = [];
  const dData: LineData[] = [];
  const jData: LineData[] = [];

  let prevK = 50;
  let prevD = 50;

  for (let i = 0; i < data.length; i++) {
    const time = data[i].time;
    // Find Lowest Low and Highest High in last n days
    let low = data[i].low;
    let high = data[i].high;
    for (let j = 0; j < n && i - j >= 0; j++) {
      low = Math.min(low, data[i - j].low);
      high = Math.max(high, data[i - j].high);
    }

    let rsv = 50;
    if (high - low !== 0) {
      rsv = ((data[i].close - low) / (high - low)) * 100;
    }

    const k = (2 / 3) * prevK + (1 / 3) * rsv;
    const d = (2 / 3) * prevD + (1 / 3) * k;
    const jVal = 3 * k - 2 * d;

    prevK = k;
    prevD = d;

    kData.push({ time, value: k });
    dData.push({ time, value: d });
    jData.push({ time, value: jVal });
  }

  return { k: kData, d: dData, j: jData };
}

// RSI: Relative Strength Index
export function calculateRSI(
  data: CandlestickData[],
  period: number = 14
): LineData[] {
  const result: LineData[] = [];
  if (data.length <= period) return result;

  // Standard RSI with Wilder's Smoothing usually, or Simple Moving Average for RS?
  // Common simpler implementation depends on platform. Let's use SMA based for simplicity first,
  // or simple recursive averaging which is standard for indicators libraries.

  let gainSum = 0;
  let lossSum = 0;

  // First period
  for (let i = 1; i <= period; i++) {
    const change = data[i].close - data[i - 1].close;
    if (change > 0) gainSum += change;
    else lossSum += Math.abs(change);
  }

  let avgGain = gainSum / period;
  let avgLoss = lossSum / period;

  // First point
  if (period < data.length) {
    const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    const rsi = 100 - 100 / (1 + rs);
    result.push({ time: data[period].time, value: rsi });
  }

  for (let i = period + 1; i < data.length; i++) {
    const change = data[i].close - data[i - 1].close;
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? Math.abs(change) : 0;

    // Smoothed avg
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;

    const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    const rsi = 100 - 100 / (1 + rs);

    result.push({ time: data[i].time, value: rsi });
  }

  return result;
}
