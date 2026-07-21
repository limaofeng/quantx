import { useCallback, useState } from 'react';
import type { MouseEvent as ReactMouseEvent } from 'react';

import type {
  StudioMenuAnchor,
  StudioMenuPlacement,
  StudioMenuState,
} from './StudioMenu';

interface OpenFromElementOptions {
  offset?: number;
  placement?: StudioMenuPlacement;
}

interface OpenAtPointerOptions {
  stopPropagation?: boolean;
}

function rectFromElement(element: Element) {
  const rect = element.getBoundingClientRect();
  return {
    bottom: rect.bottom,
    height: rect.height,
    left: rect.left,
    right: rect.right,
    top: rect.top,
    width: rect.width,
  };
}

export function useStudioMenu<TPayload>() {
  const [menu, setMenu] = useState<StudioMenuState<TPayload> | null>(null);

  const closeMenu = useCallback(() => {
    setMenu(null);
  }, []);

  const openAtPointer = useCallback(
    (
      event: ReactMouseEvent<Element>,
      payload: TPayload,
      options: OpenAtPointerOptions = {}
    ) => {
      event.preventDefault();
      if (options.stopPropagation !== false) event.stopPropagation();

      setMenu({
        anchor: {
          kind: 'point',
          x: event.clientX,
          y: event.clientY,
        },
        payload,
      });
    },
    []
  );

  const openFromElement = useCallback(
    (
      event: ReactMouseEvent<Element>,
      payload: TPayload,
      options: OpenFromElementOptions = {}
    ) => {
      event.preventDefault();
      event.stopPropagation();

      const anchor: StudioMenuAnchor = {
        kind: 'element',
        offset: options.offset,
        placement: options.placement,
        rect: rectFromElement(event.currentTarget),
      };

      setMenu({ anchor, payload });
    },
    []
  );

  return {
    closeMenu,
    menu,
    openAtPointer,
    openFromElement,
    setMenu,
  };
}
