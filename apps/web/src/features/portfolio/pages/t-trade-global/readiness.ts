export function readinessStageLabel(
  status?: string | null,
  stage?: string | null
) {
  const normalizedStatus = String(status || '').toUpperCase();
  const normalizedStage = String(stage || 'UNKNOWN').toUpperCase();
  if (normalizedStatus === 'HARD_KILL') return '做 T 自动化：紧急停止';
  if (normalizedStatus === 'BLOCKED') return '做 T 自动化：准备受阻';
  return `做 T 自动化：${normalizedStage}`;
}
