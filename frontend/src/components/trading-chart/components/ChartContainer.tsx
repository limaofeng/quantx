import React, { useRef } from 'react';
import type { LegacyRef } from 'react';

import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from '@/components/ui/resizable';

import { type SubIndicatorType } from '../utils/indicators';

interface ChartContainerProps {
  mainContainerRef: LegacyRef<HTMLDivElement>;
  activeSubs: SubIndicatorType[];
  setSubContainerRef: (
    type: SubIndicatorType,
    el: HTMLDivElement | null
  ) => void;
}

export function ChartContainer({
  mainContainerRef,
  activeSubs,
  setSubContainerRef,
}: ChartContainerProps) {
  // Simple layout logic: Main 60%, Subs share remaining 40%
  // Or: Main 70%, Subs 30%.
  // If > 2 subs, maybe Main 50%.

  const mainSize =
    activeSubs.length === 0 ? 95 : activeSubs.length > 2 ? 50 : 70;
  const subSize = (100 - mainSize) / Math.max(1, activeSubs.length);

  return (
    <div className="flex-1 relative min-h-0 w-full overflow-hidden">
      <ResizablePanelGroup direction="vertical" className="w-full h-full">
        <ResizablePanel id="main" order={0} defaultSize={mainSize} minSize={30}>
          <div ref={mainContainerRef} className="w-full h-full" />
        </ResizablePanel>

        {activeSubs.map((type, index) => (
          <React.Fragment key={type}>
            <ResizableHandle
              withHandle={false}
              className="h-px bg-slate-200/50 dark:bg-slate-800/50"
            />
            <ResizablePanel
              id={`sub-${type}`}
              order={index + 1}
              defaultSize={subSize}
              minSize={10}
            >
              <div
                ref={el => setSubContainerRef(type, el)}
                className="w-full h-full"
              />
            </ResizablePanel>
          </React.Fragment>
        ))}
      </ResizablePanelGroup>
    </div>
  );
}
