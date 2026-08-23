import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { defaultSignalPolicyForm, signalPolicyInput } from './signalPolicy';
import { TTradeSignalPolicyEditor } from './TTradeSignalPolicyEditor';

describe('TTradeSignalPolicyEditor', () => {
  it('renders one maintainable control definition for every policy field', () => {
    const { container } = render(
      <TTradeSignalPolicyEditor
        form={defaultSignalPolicyForm}
        localErrors={[]}
        onChange={vi.fn()}
        onPreview={vi.fn()}
        previewLoading={false}
        serverConfigVersion={6}
      />
    );

    const renderedFields = Array.from(
      container.querySelectorAll<HTMLElement>('[data-policy-field]'),
      element => element.dataset.policyField
    );
    expect(renderedFields).toHaveLength(100);
    expect(new Set(renderedFields).size).toBe(100);
    expect(renderedFields.sort()).toEqual(
      Object.keys(defaultSignalPolicyForm).sort()
    );

    expect(screen.getByText('数据健康与状态窗口')).toBeInTheDocument();
    expect(screen.getByText('D-1 画像安全夹取')).toBeInTheDocument();
    expect(screen.getByText('动量评分归一化')).toBeInTheDocument();
    expect(screen.getByText('显式诊断惩罚')).toBeInTheDocument();
  });

  it('emits typed list and boolean edits without implicit defaults', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <TTradeSignalPolicyEditor
        form={defaultSignalPolicyForm}
        localErrors={[]}
        onChange={onChange}
        onPreview={vi.fn()}
        previewLoading={false}
        serverConfigVersion={6}
      />
    );

    await user.click(screen.getByRole('checkbox', { name: 'CONTINUOUS_AM' }));
    await user.click(screen.getByRole('checkbox', { name: '启用动量路径' }));

    expect(onChange).toHaveBeenCalledWith('allowedSessionCodes', [
      'CONTINUOUS_PM',
    ]);
    expect(onChange).toHaveBeenCalledWith('momentumEnabled', false);
  });

  it('keeps a conflict draft visible and explains rewarming before save', () => {
    render(
      <TTradeSignalPolicyEditor
        conflictVersion={7}
        conflictPolicy={{
          ...signalPolicyInput(defaultSignalPolicyForm),
          momentumBookImbalanceScoreMaxRatio: 0.5,
        }}
        form={defaultSignalPolicyForm}
        localErrors={[]}
        onChange={vi.fn()}
        onPreview={vi.fn()}
        preview={{
          valid: true,
          configVersion: 6,
          errors: [],
          warnings: [],
          changedFields: ['candidate_score'],
          requiresRewarm: true,
          normalizedPolicy: {
            policyVersion: 'policy-v3.1',
            featureSchemaVersion: '3',
          },
        }}
        previewLoading={false}
        serverConfigVersion={6}
      />
    );
    const conflict = screen.getByRole('alert');
    expect(
      within(conflict).getByText('配置版本冲突，草稿已保留')
    ).toBeInTheDocument();
    expect(within(conflict).getByText('盘口·满分比例')).toBeInTheDocument();
    expect(within(conflict).getByText('0.5')).toBeInTheDocument();
    expect(screen.getByText('保存后需要重热')).toBeInTheDocument();
    expect(screen.getByText(/旧待确认信号会失效/)).toBeInTheDocument();
  });

  it('exposes the pure server preview with an accessible live result', () => {
    const { container } = render(
      <TTradeSignalPolicyEditor
        form={defaultSignalPolicyForm}
        localErrors={[]}
        onChange={vi.fn()}
        onPreview={vi.fn()}
        preview={{
          valid: false,
          configVersion: 6,
          errors: [
            {
              code: 'INVALID_THRESHOLD',
              field: 'candidateScore',
              message: '候选阈值必须高于预览阈值',
            },
          ],
          warnings: [],
          changedFields: [],
          requiresRewarm: false,
        }}
        previewLoading={false}
        serverConfigVersion={6}
      />
    );

    expect(
      screen.getByRole('button', { name: '验证配置' })
    ).toBeInTheDocument();
    const preview = screen.getByRole('status');
    expect(preview).toHaveAttribute('aria-live', 'polite');
    expect(preview).toHaveAttribute('aria-atomic', 'true');
    expect(preview).toHaveTextContent('存在阻断错误');
    expect(preview).toHaveTextContent('候选阈值必须高于预览阈值');
    expect(container.querySelector('[role="status"]')).not.toBeNull();
  });

  it('keeps preview feedback motion-safe for reduced-motion users', () => {
    const { container } = render(
      <TTradeSignalPolicyEditor
        form={defaultSignalPolicyForm}
        localErrors={[]}
        onChange={vi.fn()}
        onPreview={vi.fn()}
        previewLoading
        serverConfigVersion={6}
      />
    );

    const spinner = screen
      .getByRole('button', { name: '验证配置' })
      .querySelector('svg');
    expect(spinner).toHaveClass('motion-reduce:animate-none');
    expect(container.querySelector('svg')).not.toBeNull();
  });
});
