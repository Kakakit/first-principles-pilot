import {useLayoutEffect, useRef} from 'react';

/** Animate a persistent content region only when the user's section changes. */
export function usePanelTransition<T extends HTMLElement>(section:string | number) {
  const element = useRef<T>(null);
  const previous = useRef(section);
  useLayoutEffect(() => {
    if (previous.current === section) return;
    previous.current = section;
    const panel = element.current;
    if (!panel || !window.matchMedia('(prefers-reduced-motion: no-preference)').matches) return;
    const animation = panel.animate([{opacity:0.78}, {opacity:1}], {
      duration:260, easing:'ease-out'
    });
    return () => animation.cancel();
  }, [section]);
  return element;
}
