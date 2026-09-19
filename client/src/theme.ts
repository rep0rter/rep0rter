// Apply the saved appearance before CSS paints. Storage is optional.
(() => {
  type Mode = 'light' | 'dark' | 'system';
  interface Reveal {
    revision: number;
    transition: ViewTransition | null;
    animation: Animation | null;
    releaseScroll: () => void;
  }

  const root = document.documentElement;
  // The circle and glyph animation overlap; either owner may finish first.
  const scrollOwners = new Set<object>();
  const scrollKeys = new Set(['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'PageUp', 'PageDown', 'Home', 'End', ' ']);
  const isScrollKey = (event: KeyboardEvent) => {
    if (!scrollKeys.has(event.key) || event.ctrlKey || event.metaKey || event.altKey) return false;
    const target = event.target as Element | null;
    if (target?.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"])')) return false;
    return !(event.key === ' ' && target?.closest?.('a,button,summary'));
  };
  const acquireScroll = () => {
    const owner = {};
    if (!scrollOwners.size) {
      // stable gutters preserve layout on scrollbar-based desktop browsers.
      // Older engines use the measured gutter without changing scroll position.
      const gap = window.CSS?.supports('scrollbar-gutter: stable') ? 0 : Math.max(0, window.innerWidth - root.clientWidth);
      root.style.setProperty('--animation-scrollbar-gap', `${gap}px`);
      root.dataset.animationScrollLock = '';
    }
    scrollOwners.add(owner);
    return () => {
      if (!scrollOwners.delete(owner) || scrollOwners.size) return;
      delete root.dataset.animationScrollLock;
      root.style.removeProperty('--animation-scrollbar-gap');
    };
  };
  window.Rep0rterScrollLock = Object.freeze({ acquire: acquireScroll, isLocked: () => scrollOwners.size > 0, isScrollKey });
  const preventScroll = (event: Event) => { if (scrollOwners.size && event.cancelable) event.preventDefault(); };
  window.addEventListener('wheel', preventScroll, { passive: false });
  window.addEventListener('touchmove', preventScroll, { passive: false });
  document.addEventListener('keydown', event => { if (isScrollKey(event)) preventScroll(event); }, { capture: true });
  const key = 'rep0rter-theme';
  const modes: readonly string[] = ['light', 'dark', 'system'];
  const isMode = (value: unknown): value is Mode =>
    typeof value === 'string' && modes.includes(value);
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let preference: Mode = 'system';
  let revision = 0;
  let activeReveal: Reveal | null = null;
  try {
    const saved = localStorage.getItem(key);
    if (isMode(saved)) preference = saved;
  } catch (_) { /* Reading still works when browser storage is blocked. */ }

  const palette = () => preference === 'system' ? (system.matches ? 'dark' : 'light') : preference;
  const apply = () => {
    root.dataset.theme = palette();
    document.querySelectorAll<HTMLElement>('[data-theme-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === preference));
    });
    document.querySelectorAll<HTMLElement>('[data-theme-trigger]').forEach(button => {
      const label = button.dataset['theme' + preference[0]!.toUpperCase() + preference.slice(1)];
      const description = `${button.dataset.themeLabel}: ${label}`;
      button.dataset.themePreference = preference;
      button.setAttribute('aria-label', description);
      button.setAttribute('title', description);
      button.querySelectorAll<HTMLElement>('[data-theme-icon]').forEach(icon => {
        icon.hidden = icon.dataset.themeIcon !== preference;
      });
    });
    // Theme-aware artwork swaps inside the same view transition snapshot.
    return window.Rep0rterImages?.sync();
  };
  const announce = (name: string, detail?: unknown) => {
    if (typeof CustomEvent === 'function' && typeof document.dispatchEvent === 'function') {
      document.dispatchEvent(new CustomEvent(name, { detail }));
    }
  };
  const clearReveal = (reveal: Reveal) => {
    // A skipped transition can finish after the next one has already started.
    if (activeReveal !== reveal) return;
    reveal.animation?.cancel();
    reveal.releaseScroll();
    activeReveal = null;
    delete root.dataset.themeTransition;
    root.style.removeProperty('--theme-reveal-x');
    root.style.removeProperty('--theme-reveal-y');
  };
  const cancelReveal = () => {
    revision += 1;
    const reveal = activeReveal;
    if (reveal) {
      reveal.transition?.skipTransition();
      clearReveal(reveal);
    }
    announce('rep0rter:theme-cancel');
  };
  const applyImmediately = () => {
    cancelReveal();
    apply();
  };
  const revealFrom = (trigger: Element | null | undefined) => {
    cancelReveal();
    if (root.dataset.theme === palette() || reducedMotion.matches ||
        typeof document.startViewTransition !== 'function' ||
        typeof root.animate !== 'function' || !trigger?.getBoundingClientRect) {
      apply();
      return;
    }
    const bounds = trigger.getBoundingClientRect();
    const x = bounds.left + bounds.width / 2;
    const y = bounds.top + bounds.height / 2;
    const radius = Math.hypot(
      Math.max(x, window.innerWidth - x),
      Math.max(y, window.innerHeight - y)
    );
    const reveal: Reveal = { revision, transition: null, animation: null, releaseScroll: acquireScroll() };
    activeReveal = reveal;
    // Suppress separately named snapshots before BOTH captures, not just after
    // the theme changes. Article navigation keeps its own transition rules.
    // Seed the CSS snapshot mask with the same viewport origin before capture.
    // Its first frame must never fall back to the viewport's center.
    root.style.setProperty('--theme-reveal-x', `${x}px`);
    root.style.setProperty('--theme-reveal-y', `${y}px`);
    root.dataset.themeTransition = 'reveal';
    try {
      const transition = document.startViewTransition(() => {
        if (revision === reveal.revision) return apply();
      });
      reveal.transition = transition;
      // Attach rejection handlers immediately: skipTransition rejects ready.
      transition.updateCallbackDone.catch(() => {
        if (revision === reveal.revision) return apply();
      });
      transition.finished.then(() => clearReveal(reveal), () => clearReveal(reveal));
      transition.ready.then(() => {
        if (activeReveal !== reveal || revision !== reveal.revision) return;
        try {
          reveal.animation = root.animate([
            { clipPath: `circle(0px at ${x}px ${y}px)` },
            { clipPath: `circle(${radius}px at ${x}px ${y}px)` },
          ], {
            duration: 500,
            easing: 'ease-in-out',
            fill: 'both',
            pseudoElement: '::view-transition-new(root)',
          });
          reveal.animation!.finished.catch(() => {});
          announce('rep0rter:theme-reveal', { x, y, radius, duration: 500 });
        } catch (_) {
          transition.skipTransition();
          clearReveal(reveal);
        }
      }, () => {
        if (revision === reveal.revision) apply();
        clearReveal(reveal);
      });
    } catch (_) {
      clearReveal(reveal);
      apply();
    }
  };
  apply();
  system.addEventListener('change', () => {
    if (preference === 'system') applyImmediately();
  });
  reducedMotion.addEventListener('change', () => {
    if (reducedMotion.matches) applyImmediately();
  });
  window.addEventListener('pagehide', applyImmediately);
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    preference = isMode(event.newValue) ? event.newValue : 'system';
    applyImmediately();
  });
  const bound = new WeakSet();
  const menus = () => [...document.querySelectorAll<HTMLDetailsElement>('.preference-menu')];
  const initializeControls = () => {
    document.querySelectorAll<HTMLElement>('[data-theme-controls]').forEach(group => { group.hidden = false; });
    document.querySelectorAll<HTMLElement>('[data-theme-choice]').forEach(button => {
      if (bound.has(button)) return;
      bound.add(button);
      button.addEventListener('click', () => {
        const chosen = button.dataset.themeChoice;
        if (!isMode(chosen)) return;
        preference = chosen;
        try { localStorage.setItem(key, preference); } catch (_) { /* Session-only choice. */ }
        const menu = button.closest<HTMLDetailsElement>('.preference-menu');
        const trigger = menu?.querySelector<HTMLElement>('summary');
        if (menu) {
          menu.open = false;
          trigger?.focus({ preventScroll: true });
        }
        revealFrom(trigger);
      });
    });
    menus().forEach(menu => {
      // Every .preference-menu is rendered with a <summary> by _ui.html.
      const trigger = menu.querySelector('summary')!;
      if (bound.has(trigger)) return;
      bound.add(trigger);
      trigger.addEventListener('click', () => {
        menus().forEach(other => { if (other !== menu) other.open = false; });
      });
    });
    apply();
  };
  document.addEventListener('DOMContentLoaded', initializeControls);
  // Language replacement retains this controller, but creates new controls.
  document.addEventListener('rep0rter:before-language', applyImmediately);
  document.addEventListener('rep0rter:language-applied', initializeControls);
  document.addEventListener('pointerdown', event => {
    menus().forEach(menu => { if (!menu.contains(event.target as Node | null)) menu.open = false; });
  });
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const open = menus().find(menu => menu.open);
    if (!open) return;
    event.preventDefault();
    open.open = false;
    open.querySelector('summary')!.focus({ preventScroll: true });
  });
})();
