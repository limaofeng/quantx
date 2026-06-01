// 通用基础类型定义

export interface BaseEntity {
  id: string;
  createdAt?: string;
  updatedAt?: string;
}

export interface ApiResponse<T = any> {
  data: T;
  message?: string;
  success: boolean;
}

export interface PaginatedResponse<T = any> {
  data: T[];
  total: number;
  page: number;
  pageSize: number;
}

export interface SelectOption {
  label: string;
  value: string | number;
  disabled?: boolean;
}

export type Status = 'active' | 'inactive' | 'pending' | 'completed' | 'error';

export interface DateRange {
  start: Date;
  end: Date;
}
