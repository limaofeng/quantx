import { readinessStageLabel } from '@/features/portfolio/pages/t-trade-global/readiness';

describe('T-trade readiness labels', () => {
  it('keeps account preparation separate from the assistant rollout stage', () => {
    expect(readinessStageLabel('PREPARING', 'LIVE')).toBe('做 T 自动化：LIVE');
    expect(readinessStageLabel('PREPARING', 'CANARY')).toBe(
      '做 T 自动化：CANARY'
    );
  });

  it('uses explicit assistant wording for ready and blocked states', () => {
    expect(readinessStageLabel('READY', 'LIVE')).toBe('做 T 自动化：LIVE');
    expect(readinessStageLabel('BLOCKED', 'SHADOW')).toBe(
      '做 T 自动化：准备受阻'
    );
    expect(readinessStageLabel('HARD_KILL', 'LIVE')).toBe(
      '做 T 自动化：紧急停止'
    );
  });
});
