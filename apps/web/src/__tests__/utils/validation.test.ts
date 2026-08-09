// 验证工具函数测试
import {
  isValidStockCode,
  isValidEmail,
  isValidPhoneNumber,
  isValidPrice,
  isValidQuantity,
  isValidPercentage,
  isInRange,
  isValidStringLength,
  schemas,
} from '@/shared/utils/validation';

describe('Validation Utils', () => {
  describe('isValidStockCode', () => {
    it('validates correct stock code', () => {
      expect(isValidStockCode('000001')).toBe(true);
      expect(isValidStockCode('600519')).toBe(true);
    });

    it('rejects invalid stock code', () => {
      expect(isValidStockCode('00001')).toBe(false); // too short
      expect(isValidStockCode('0000001')).toBe(false); // too long
      expect(isValidStockCode('ABC123')).toBe(false); // contains letters
    });
  });

  describe('isValidEmail', () => {
    it('validates correct email', () => {
      expect(isValidEmail('test@example.com')).toBe(true);
      expect(isValidEmail('user.name@domain.co.uk')).toBe(true);
    });

    it('rejects invalid email', () => {
      expect(isValidEmail('invalid-email')).toBe(false);
      expect(isValidEmail('@domain.com')).toBe(false);
      expect(isValidEmail('test@')).toBe(false);
    });
  });

  describe('isValidPhoneNumber', () => {
    it('validates correct phone number', () => {
      expect(isValidPhoneNumber('13812345678')).toBe(true);
      expect(isValidPhoneNumber('18912345678')).toBe(true);
    });

    it('rejects invalid phone number', () => {
      expect(isValidPhoneNumber('12812345678')).toBe(false); // starts with 12
      expect(isValidPhoneNumber('1381234567')).toBe(false); // too short
      expect(isValidPhoneNumber('138123456789')).toBe(false); // too long
    });
  });

  describe('isValidPrice', () => {
    it('validates correct price', () => {
      expect(isValidPrice(12.34)).toBe(true);
      expect(isValidPrice(0.0001)).toBe(true);
    });

    it('rejects invalid price', () => {
      expect(isValidPrice(0)).toBe(false); // zero
      expect(isValidPrice(-10)).toBe(false); // negative
      expect(isValidPrice(12.12345)).toBe(false); // more than 4 decimals
      expect(isValidPrice(Infinity)).toBe(false); // infinite
    });
  });

  describe('isValidQuantity', () => {
    it('validates correct quantity', () => {
      expect(isValidQuantity(100)).toBe(true);
      expect(isValidQuantity(1)).toBe(true);
    });

    it('rejects invalid quantity', () => {
      expect(isValidQuantity(0)).toBe(false); // zero
      expect(isValidQuantity(-100)).toBe(false); // negative
      expect(isValidQuantity(100.5)).toBe(false); // decimal
    });
  });

  describe('isValidPercentage', () => {
    it('validates correct percentage', () => {
      expect(isValidPercentage(0)).toBe(true);
      expect(isValidPercentage(50)).toBe(true);
      expect(isValidPercentage(100)).toBe(true);
    });

    it('rejects invalid percentage', () => {
      expect(isValidPercentage(-1)).toBe(false); // negative
      expect(isValidPercentage(101)).toBe(false); // over 100
      expect(isValidPercentage(Infinity)).toBe(false); // infinite
    });
  });

  describe('isInRange', () => {
    it('validates value in range', () => {
      expect(isInRange(5, 0, 10)).toBe(true);
      expect(isInRange(0, 0, 10)).toBe(true);
      expect(isInRange(10, 0, 10)).toBe(true);
    });

    it('rejects value out of range', () => {
      expect(isInRange(-1, 0, 10)).toBe(false);
      expect(isInRange(11, 0, 10)).toBe(false);
      expect(isInRange(Infinity, 0, 10)).toBe(false);
    });
  });

  describe('isValidStringLength', () => {
    it('validates string length', () => {
      expect(isValidStringLength('hello', 0, 10)).toBe(true);
      expect(isValidStringLength('', 0, 10)).toBe(true);
      expect(isValidStringLength('1234567890', 0, 10)).toBe(true);
    });

    it('rejects invalid string length', () => {
      expect(isValidStringLength('hello', 6, 10)).toBe(false); // too short
      expect(isValidStringLength('hello world!', 0, 10)).toBe(false); // too long
    });
  });

  describe('schemas', () => {
    it('validates stock code schema', () => {
      expect(schemas.stockCode.safeParse('000001').success).toBe(true);
      expect(schemas.stockCode.safeParse('ABC123').success).toBe(false);
    });

    it('validates price schema', () => {
      expect(schemas.price.safeParse(12.34).success).toBe(true);
      expect(schemas.price.safeParse(-10).success).toBe(false);
    });

    it('validates quantity schema', () => {
      expect(schemas.quantity.safeParse(100).success).toBe(true);
      expect(schemas.quantity.safeParse(-100).success).toBe(false);
    });

    it('validates percentage schema', () => {
      expect(schemas.percentage.safeParse(50).success).toBe(true);
      expect(schemas.percentage.safeParse(150).success).toBe(false);
    });

    it('validates email schema', () => {
      expect(schemas.email.safeParse('test@example.com').success).toBe(true);
      expect(schemas.email.safeParse('invalid-email').success).toBe(false);
    });
  });
});
