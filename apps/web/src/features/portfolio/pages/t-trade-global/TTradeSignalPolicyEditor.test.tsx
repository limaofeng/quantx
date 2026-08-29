import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { defaultSignalPolicyForm, signalPolicyInput } from './signalPolicy';
import { TTradeSignalPolicyEditor } from './TTradeSignalPolicyEditor';

describe('TTradeSignalPolicyEditor', () => {
  it('keeps all 100 policy fields reachable without rendering a form wall', async () => {
    const user = userEvent.setup();
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

    expect(
      container.querySelectorAll<HTMLElement>('[data-policy-field]')
    ).toHaveLength(0);
    expect(screen.getByText('双路径策略蓝图')).toBeInTheDocument();

    const renderedFields = new Set<string>();
    for (const moduleName of [
      '数据健康',
      '交易时段',
      '回撤反弹路径',
      '早期动量路径',
      'D-1 画像夹取',
      '正向贡献权重',
      '回撤评分归一化',
      '动量评分归一化',
      '诊断惩罚',
      '候选生命周期',
    ]) {
      await user.click(
        screen.getByRole('button', { name: new RegExp(`^${moduleName}，`) })
      );
      container
        .querySelectorAll<HTMLElement>('[data-policy-field]')
        .forEach(element => {
          if (element.dataset.policyField) {
            renderedFields.add(element.dataset.policyField);
          }
        });
    }

    expect(renderedFields.size).toBe(100);
    expect([...renderedFields].sort()).toEqual(
      Object.keys(defaultSignalPolicyForm).sort()
    );

    expect(screen.getByText('阈值与候选生命周期')).toBeInTheDocument();
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

    await user.click(screen.getByRole('button', { name: /^交易时段，/ }));
    await user.click(screen.getByRole('checkbox', { name: 'CONTINUOUS_AM' }));
    await user.click(screen.getByRole('button', { name: /^早期动量路径，/ }));
    await user.click(screen.getByRole('checkbox', { name: '启用动量路径' }));

    expect(onChange).toHaveBeenCalledWith('allowedSessionCodes', [
      'CONTINUOUS_PM',
    ]);
    expect(onChange).toHaveBeenCalledWith('momentumEnabled', false);
  });

  it('renders the approved weight editor with independent 100-point totals', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    function ControlledEditor() {
      const [form, setForm] = useState(defaultSignalPolicyForm);
      return (
        <TTradeSignalPolicyEditor
          form={form}
          localErrors={[]}
          onChange={(field, value) => {
            onChange(field, value);
            setForm(current => ({ ...current, [field]: value }));
          }}
          onPreview={vi.fn()}
          previewLoading={false}
          serverConfigVersion={6}
        />
      );
    }
    render(<ControlledEditor />);

    await user.click(screen.getByRole('button', { name: /^正向贡献权重，/ }));

    expect(
      screen.getByRole('heading', { name: '正向贡献权重' })
    ).toBeInTheDocument();
    expect(screen.getAllByText('权重有效')).toHaveLength(2);
    expect(
      screen.getAllByText(
        (_content, element) =>
          element?.tagName === 'SPAN' &&
          element.textContent?.trim() === '合计 100 / 100'
      )
    ).toHaveLength(2);

    await user.clear(screen.getByLabelText('深度'));
    await user.type(screen.getByLabelText('深度'), '24');

    expect(onChange).toHaveBeenCalledWith('pullbackDepthWeight', '24');
    expect(screen.getAllByText('已修改 1 项').length).toBeGreaterThan(0);
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

  it('exposes the pure server preview and routes issues to their module', async () => {
    const user = userEvent.setup();
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

    await user.click(screen.getByRole('button', { name: /前往候选生命周期/ }));
    expect(
      screen.getByRole('heading', { name: '阈值与候选生命周期' })
    ).toBeInTheDocument();
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
