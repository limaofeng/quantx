export function readinessStageLabel(
  status?: string | null,
  stage?: string | null
) {
  const normalizedStage = String(stage || 'UNKNOWN').toUpperCase();
  return String(status || '').toUpperCase() === 'PREPARING'
    ? `门禁准备中（灰度 ${normalizedStage}）`
    : normalizedStage;
}
