// Same-page sign-in; Google authentication remains a CSRF-protected form POST.
(() => {
  const root = document.documentElement;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const pendingKey = 'rep0rter-login-return';
  let dismiss: (() => void) | null = null;
  const restoreReading = () => {
    try {
      const raw = sessionStorage.getItem(pendingKey);
      if (!raw) return;
      sessionStorage.removeItem(pendingKey);
      const saved = JSON.parse(raw) as { url: string; y: number; at: number };
      if (saved.url !== location.href || Date.now() - saved.at > 900_000 || !Number.isFinite(saved.y) || saved.y < 0) return;
      let interrupted = false;
      const cancel = () => { interrupted = true; };
      const events = ['wheel', 'touchstart', 'keydown'] as const;
      events.forEach(name => window.addEventListener(name, cancel, { once: true, passive: true }));
      // Initial ?lang replacement is asynchronous; restore only after its layout.
      const languageReady = new Promise<void>(resolve => {
        if (root.getAttribute('aria-busy') !== 'true') { resolve(); return; }
        const done = () => { observer.disconnect(); clearTimeout(timeout); resolve(); };
        const observer = new MutationObserver(() => { if (root.getAttribute('aria-busy') !== 'true') done(); });
        const timeout = setTimeout(done, 8000);
        observer.observe(root, { attributes: true, attributeFilter: ['aria-busy'] });
      });
      void languageReady.then(() => Promise.race([document.fonts.ready, new Promise(resolve => setTimeout(resolve, 600))]))
        .then(() => requestAnimationFrame(() => {
          if (!interrupted && root.getAttribute('aria-busy') !== 'true') window.scrollTo({ top: saved.y, behavior: 'instant' });
          events.forEach(name => window.removeEventListener(name, cancel));
        }));
    } catch { /* Storage is optional; the server still returns to the same URL. */ }
  };
  if (document.readyState === 'loading' || document.readyState === 'interactive') {
    document.addEventListener('DOMContentLoaded', restoreReading, { once: true });
  } else restoreReading();
  window.addEventListener('pageshow', event => { if (event.persisted) restoreReading(); });
  document.addEventListener('click', event => {
    const trigger = (event.target as Element | null)?.closest<HTMLAnchorElement>('a[data-sign-in]');
    if (!trigger || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || typeof HTMLDialogElement === 'undefined' || !HTMLDialogElement.prototype.showModal) return;
    event.preventDefault();
    dismiss?.();
    const url = new URL(trigger.href);
    url.searchParams.set('return_to', location.pathname + location.search + location.hash);
    url.searchParams.set('lang', document.documentElement.lang);
    const fallbackURL = url.href;
    url.searchParams.set('fragment', '1');
    const dialog = document.createElement('dialog');
    dialog.className = 'login-dialog';
    dialog.setAttribute('aria-label', trigger.textContent?.trim() || 'Sign in');
    const toolbar = document.createElement('div');
    toolbar.className = 'login-toolbar';
    const close = document.createElement('button');
    close.className = 'login-close';
    close.type = 'button';
    close.autofocus = true;
    close.setAttribute('aria-label', trigger.dataset.close || 'Close');
    close.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg>';
    toolbar.append(close);
    const content = document.createElement('div');
    dialog.append(toolbar, content);
    document.body.append(dialog);
    let disposed = false;
    let closing = false;
    let controller: AbortController | null = null;
    let animation: Animation | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const cleanup = () => {
      if (disposed) return;
      disposed = true;
      controller?.abort();
      clearTimeout(timer);
      animation?.cancel();
      dialog.close();
      dialog.remove();
      delete root.dataset.loginOpen;
      if (trigger.isConnected) trigger.focus({ preventScroll: true });
      if (dismiss === cleanup) dismiss = null;
    };
    dismiss = cleanup;
    const closeDialog = () => {
      if (closing || disposed) return;
      closing = true;
      controller?.abort();
      if (reduced.matches || !dialog.animate) { cleanup(); return; }
      animation?.cancel();
      animation = dialog.animate([{ opacity: 1, transform: 'scale(1)' }, { opacity: 0, transform: 'scale(.98)' }], { duration: 160, easing: 'ease-out', fill: 'forwards' });
      void animation.finished.then(cleanup, cleanup);
    };
    const load = async () => {
      controller?.abort();
      const request = new AbortController();
      controller = request;
      clearTimeout(timer);
      timer = setTimeout(() => request.abort(), 8000);
      content.className = 'login-status';
      content.replaceChildren();
      const status = document.createElement('p');
      status.setAttribute('role', 'status');
      status.textContent = trigger.dataset.loading || 'Loading…';
      content.append(status);
      content.setAttribute('aria-busy', 'true');
      try {
        const response = await fetch(url, { signal: request.signal, credentials: 'same-origin', cache: 'no-store' });
        if (!response.ok || !response.headers.get('content-type')?.includes('text/html')) throw Error('Sign-in unavailable');
        const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
        const card = parsed.querySelector<HTMLElement>('[data-auth-card]');
        if (!card) throw Error('Missing sign-in card');
        if (disposed || closing || controller !== request) return;
        content.className = '';
        content.replaceChildren(document.importNode(card, true));
        dialog.setAttribute('aria-labelledby', 'login-title');
      } catch {
        if (disposed || closing || controller !== request) return;
        status.textContent = trigger.dataset.error || 'Sign-in unavailable';
        status.setAttribute('role', 'alert');
        const actions = document.createElement('div');
        actions.className = 'login-status-actions';
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'button button-secondary';
        retry.textContent = trigger.dataset.retry || 'Try again';
        retry.addEventListener('click', () => { close.focus({ preventScroll: true }); void load(); });
        const fallback = document.createElement('a');
        fallback.href = fallbackURL;
        fallback.textContent = trigger.dataset.fallback || 'Open sign-in page';
        actions.append(retry, fallback);
        content.append(actions);
      } finally {
        if (controller === request) { clearTimeout(timer); content.removeAttribute('aria-busy'); }
      }
    };
    close.addEventListener('click', closeDialog);
    dialog.addEventListener('cancel', event => { event.preventDefault(); closeDialog(); });
    let backdropPress = false;
    dialog.addEventListener('pointerdown', event => { backdropPress = event.target === dialog; });
    dialog.addEventListener('click', event => {
      if (backdropPress && event.target === dialog) closeDialog();
      if ((event.target as Element).closest('[data-login-dismiss]')) { event.preventDefault(); closeDialog(); }
    });
    dialog.addEventListener('close', cleanup);
    dialog.addEventListener('submit', event => {
      if (!(event.target as Element).matches('[data-login-form]')) return;
      try { sessionStorage.setItem(pendingKey, JSON.stringify({ url: location.href, y: scrollY, at: Date.now() })); }
      catch { /* Authentication works with storage blocked. */ }
    });
    root.dataset.loginOpen = '';
    dialog.showModal();
    close.focus({ preventScroll: true });
    if (!reduced.matches && dialog.animate) {
      animation = dialog.animate([{ opacity: 0, transform: 'translateY(10px) scale(.97)' }, { opacity: 1, transform: 'translateY(0) scale(1)' }], { duration: 300, easing: 'cubic-bezier(.2,.9,.2,1.06)' });
      void animation.finished.catch(() => {});
    }
    void load();
  });
  document.addEventListener('rep0rter:before-language', () => dismiss?.());
  window.addEventListener('pagehide', () => dismiss?.());
})();
