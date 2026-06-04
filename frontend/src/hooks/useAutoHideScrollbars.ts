import { useEffect } from 'react';

const SCROLLBAR_ACTIVE_CLASS = 'scrollbar-active';
const SCROLLBAR_HIDE_DELAY_MS = 1000;

function getScrollElement(target: EventTarget | null) {
  if (target instanceof Document) {
    return target.scrollingElement || target.documentElement;
  }
  if (target instanceof Element) {
    return target;
  }
  return null;
}

export function useAutoHideScrollbars() {
  useEffect(() => {
    const timers = new Map<Element, number>();

    const showScrollbarForScroll = (event: Event) => {
      const element = getScrollElement(event.target);
      if (!element) return;

      element.classList.add(SCROLLBAR_ACTIVE_CLASS);

      const existingTimer = timers.get(element);
      if (existingTimer !== undefined) {
        window.clearTimeout(existingTimer);
      }

      const nextTimer = window.setTimeout(() => {
        element.classList.remove(SCROLLBAR_ACTIVE_CLASS);
        timers.delete(element);
      }, SCROLLBAR_HIDE_DELAY_MS);
      timers.set(element, nextTimer);
    };

    document.addEventListener('scroll', showScrollbarForScroll, true);

    return () => {
      document.removeEventListener('scroll', showScrollbarForScroll, true);
      timers.forEach(timer => window.clearTimeout(timer));
      timers.clear();
    };
  }, []);
}
