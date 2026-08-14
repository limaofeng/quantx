export function ageSecondsLabel(value?: number | null) {
  if (value === null || value === undefined) return '无记录';
  if (!Number.isFinite(value) || value < 0) return '时间异常';
  const seconds = Math.floor(value);
  if (seconds < 60) return `${seconds} 秒前`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分钟前`;
  return `${Math.floor(minutes / 60)} 小时前`;
}
