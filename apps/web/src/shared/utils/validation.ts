// 验证工具函数

import { z } from 'zod';

/**
 * 股票代码验证
 */
export function isValidStockCode(code: string): boolean {
  // 中国股票代码格式：6位数字
  const stockCodeRegex = /^\d{6}$/;
  return stockCodeRegex.test(code);
}

/**
 * 邮箱验证
 */
export function isValidEmail(email: string): boolean {
  const emailSchema = z.string().email();
  return emailSchema.safeParse(email).success;
}

/**
 * 手机号验证（中国）
 */
export function isValidPhoneNumber(phone: string): boolean {
  const phoneRegex = /^1[3-9]\d{9}$/;
  return phoneRegex.test(phone);
}

/**
 * 价格验证（必须为正数，最多4位小数）
 */
export function isValidPrice(price: number): boolean {
  return price > 0 && Number.isFinite(price) && (price * 10000) % 1 === 0;
}

/**
 * 数量验证（必须为正整数）
 */
export function isValidQuantity(quantity: number): boolean {
  return Number.isInteger(quantity) && quantity > 0;
}

/**
 * 百分比验证（0-100之间）
 */
export function isValidPercentage(percentage: number): boolean {
  return Number.isFinite(percentage) && percentage >= 0 && percentage <= 100;
}

/**
 * 通用数字范围验证
 */
export function isInRange(value: number, min: number, max: number): boolean {
  return Number.isFinite(value) && value >= min && value <= max;
}

/**
 * 字符串长度验证
 */
export function isValidStringLength(
  str: string,
  minLength = 0,
  maxLength = Infinity
): boolean {
  return str.length >= minLength && str.length <= maxLength;
}

// Zod schemas for common validations
export const schemas = {
  stockCode: z.string().regex(/^\d{6}$/, '股票代码必须为6位数字'),

  price: z
    .number()
    .positive('价格必须为正数')
    .refine(val => (val * 10000) % 1 === 0, '价格最多支持4位小数'),

  quantity: z.number().int('数量必须为整数').positive('数量必须为正数'),

  percentage: z
    .number()
    .min(0, '百分比不能小于0')
    .max(100, '百分比不能大于100'),

  email: z.string().email('邮箱格式不正确'),

  phone: z.string().regex(/^1[3-9]\d{9}$/, '手机号格式不正确'),

  password: z
    .string()
    .min(8, '密码至少8位')
    .regex(/^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)/, '密码必须包含大小写字母和数字'),
} as const;
