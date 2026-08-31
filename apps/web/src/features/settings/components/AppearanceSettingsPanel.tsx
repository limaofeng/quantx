import { Monitor, Rows3 } from 'lucide-react';

import {
  StudioPageDescription,
  StudioPageStack,
  StudioPageTitle,
  StudioPanel,
  StudioPanelContent,
} from '@/components/ui/studio-layout';
import { useUiDensity } from '@/components/UiDensityProvider';
import { type UiDensity } from '@/core/ui-density';
import { cn } from '@/utils/cn';

const options = [
  {
    value: 'standard',
    label: '标准',
    detail: '默认 · 保留当前字体与间距',
    description: 'Inter 界面字体，13px 正文，保留现有控件和工作区尺寸。',
    icon: Monitor,
  },
  {
    value: 'compact',
    label: '紧凑',
    detail: 'IDE 密度 · 同屏显示更多内容',
    description: '系统 UI 字体，正文仍为 13px，缩小行高、控件和留白。',
    icon: Rows3,
  },
] satisfies Array<{
  value: UiDensity;
  label: string;
  detail: string;
  description: string;
  icon: typeof Monitor;
}>;

export function AppearanceSettingsPanel() {
  const { density, setDensity } = useUiDensity();

  return (
    <StudioPageStack>
      <header>
        <StudioPageTitle>外观</StudioPageTitle>
        <StudioPageDescription>
          调整界面显示密度，保留工作区布局、颜色和所有交易操作。
        </StudioPageDescription>
      </header>
      <StudioPanel>
        <StudioPanelContent>
          <fieldset aria-describedby="ui-density-description">
            <legend className="text-ui-title font-semibold">界面密度</legend>
            <p
              id="ui-density-description"
              className="mt-1 text-ui-caption text-slate-400"
            >
              选择后立即生效，无需刷新；仅记住当前浏览器、当前站点的选择。
            </p>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              {options.map(option => {
                const Icon = option.icon;
                const selected = density === option.value;
                return (
                  <label
                    key={option.value}
                    className={cn(
                      'flex cursor-pointer items-start gap-3 rounded-panel border p-ui-panel transition-colors',
                      selected
                        ? 'border-blue-400/40 bg-blue-500/10'
                        : 'border-white/10 hover:border-blue-400/30 hover:bg-white/[0.03]'
                    )}
                  >
                    <input
                      type="radio"
                      name="ui-density"
                      value={option.value}
                      aria-label={option.label}
                      checked={selected}
                      onChange={() => setDensity(option.value)}
                      className="mt-0.5 h-4 w-4 shrink-0 accent-blue-500 focus-visible:ring-2 focus-visible:ring-blue-400/70"
                    />
                    <span className="min-w-0">
                      <span className="flex items-center gap-2 text-ui-body font-semibold text-slate-100">
                        <Icon className="h-4 w-4 text-blue-300" aria-hidden />
                        {option.label}
                      </span>
                      <span className="mt-1 block text-ui-caption text-slate-400">
                        {option.detail}
                      </span>
                      <span className="mt-2 block text-ui-body text-slate-300">
                        {option.description}
                      </span>
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>
          <p role="status" className="mt-3 text-ui-caption text-slate-400">
            当前使用{density === 'compact' ? '紧凑' : '标准'}
            模式。金融数字、代码和时间保持等宽字体，侧栏宽度不变。
          </p>
        </StudioPanelContent>
      </StudioPanel>
    </StudioPageStack>
  );
}
