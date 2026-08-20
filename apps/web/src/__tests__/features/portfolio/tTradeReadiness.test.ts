import { readinessStageLabel } from '@/features/portfolio/pages/t-trade-global/readiness';

describe('T-trade readiness labels', () => {
  it('keeps the actual rollout stage while automation gates are preparing', () => {
    expect(readinessStageLabel('PREPARING', 'LIVE')).toBe(
      '门禁准备中（灰度 LIVE）'
    );
    expect(readinessStageLabel('PREPARING', 'CANARY')).toBe(
      '门禁准备中（灰度 CANARY）'
    );
  });

  it('uses the rollout stage for non-preparing states', () => {
    expect(readinessStageLabel('READY', 'LIVE')).toBe('LIVE');
  });
});
