// 格式化工具函数测试
import {
  formatCurrency,
  formatPercent,
  formatNumber,
  formatStockCode,
  formatVolume,
  formatMarketCap,
} from '@/shared/utils/format';

describe('Format Utils', () => {
  describe('formatCurrency', () => {
    it('formats currency with default CNY', () => {
      expect(formatCurrency(1234.56)).toBe('¥1,234.56');
    });

    it('formats currency with custom currency', () => {
      expect(formatCurrency(1234.56, 'USD')).toBe('US$1,234.56');
    });

    it('handles zero value', () => {
      expect(formatCurrency(0)).toBe('¥0.00');
    });

    it('handles negative value', () => {
      expect(formatCurrency(-1234.56)).toBe('-¥1,234.56');
    });
  });

  describe('formatPercent', () => {
    it('formats percentage with default 2 decimal places', () => {
      expect(formatPercent(12.3456)).toBe('12.35%');
    });

    it('formats percentage with custom decimal places', () => {
      expect(formatPercent(12.3456, 1)).toBe('12.3%');
    });

    it('handles zero percentage', () => {
      expect(formatPercent(0)).toBe('0.00%');
    });

    it('handles negative percentage', () => {
      expect(formatPercent(-5.67)).toBe('-5.67%');
    });
  });

  describe('formatNumber', () => {
    it('formats number with default 2 decimal places', () => {
      expect(formatNumber(1234.5678)).toBe('1,234.57');
    });

    it('formats number with custom options', () => {
      expect(formatNumber(1234.5678, { maximumFractionDigits: 0 })).toBe(
        '1,235'
      );
    });
  });

  describe('formatStockCode', () => {
    it('returns code without exchange', () => {
      expect(formatStockCode('000001')).toBe('000001');
    });

    it('adds exchange prefix', () => {
      expect(formatStockCode('000001', 'SZ')).toBe('SZ:000001');
    });

    it('does not duplicate exchange prefix', () => {
      expect(formatStockCode('SZ:000001', 'SZ')).toBe('SZ:000001');
    });
  });

  describe('formatVolume', () => {
    it('formats volume in K', () => {
      expect(formatVolume(1500)).toBe('1.5K');
    });

    it('formats volume in M', () => {
      expect(formatVolume(1500000)).toBe('1.5M');
    });

    it('formats volume in B', () => {
      expect(formatVolume(1500000000)).toBe('1.5B');
    });

    it('returns raw number for small volumes', () => {
      expect(formatVolume(500)).toBe('500');
    });
  });

  describe('formatMarketCap', () => {
    it('formats market cap in 万', () => {
      expect(formatMarketCap(50000)).toBe('5.00万');
    });

    it('formats market cap in 亿', () => {
      expect(formatMarketCap(500000000)).toBe('5.00亿');
    });

    it('formats market cap in 万亿', () => {
      expect(formatMarketCap(5000000000000)).toBe('5.00万亿');
    });

    it('returns currency format for small values', () => {
      expect(formatMarketCap(5000)).toBe('¥5,000.00');
    });
  });
});
