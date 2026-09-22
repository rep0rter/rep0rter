"use strict";
// Same-page sign-in; Google authentication remains a CSRF-protected form POST.
(() => {
    const root = document.documentElement;
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    const pendingKey = 'rep0rter-login-return';
    let dismiss = null;
    const restoreReading = () => {
        try {
            const raw = sessionStorage.getItem(pendingKey);
            if (!raw)
                return;
            sessionStorage.removeItem(pendingKey);
            const saved = JSON.parse(raw);
            if (saved.url !== location.href || Date.now() - saved.at > 900_000 || !Number.isFinite(saved.y) || saved.y < 0)
                return;
            let interrupted = false;
            const cancel = () => { interrupted = true; };
            const events = ['wheel', 'touchstart', 'keydown'];
            events.forEach(name => window.addEventListener(name, cancel, { once: true, passive: true }));
            // Initial ?lang replacement is asynchronous; restore only after its layout.
            const languageReady = new Promise(resolve => {
                if (root.getAttribute('aria-busy') !== 'true') {
                    resolve();
                    return;
                }
                const done = () => { observer.disconnect(); clearTimeout(timeout); resolve(); };
                const observer = new MutationObserver(() => { if (root.getAttribute('aria-busy') !== 'true')
                    done(); });
                const timeout = setTimeout(done, 8000);
                observer.observe(root, { attributes: true, attributeFilter: ['aria-busy'] });
            });
            void languageReady.then(() => Promise.race([document.fonts.ready, new Promise(resolve => setTimeout(resolve, 600))]))
                .then(() => requestAnimationFrame(() => {
                if (!interrupted && root.getAttribute('aria-busy') !== 'true')
                    window.scrollTo({ top: saved.y, behavior: 'instant' });
                events.forEach(name => window.removeEventListener(name, cancel));
            }));
        }
        catch { /* Storage is optional; the server still returns to the same URL. */ }
    };
    if (document.readyState === 'loading' || document.readyState === 'interactive') {
        document.addEventListener('DOMContentLoaded', restoreReading, { once: true });
    }
    else
        restoreReading();
    window.addEventListener('pageshow', event => { if (event.persisted)
        restoreReading(); });
    document.addEventListener('click', event => {
        const trigger = event.target?.closest('a[href]');
        if (!trigger)
            return;
        const targetURL = new URL(trigger.href);
        const entry = document.querySelector('[data-sign-in]');
        const authoring = !document.body.classList.contains('account-page') && !trigger.closest('[data-auth-card]') && entry &&
            targetURL.origin === location.origin && ['/write', '/submit', '/projects'].includes(targetURL.pathname);
        if (!trigger.matches('[data-sign-in]') && !authoring)
            return;
        const labels = entry?.dataset || trigger.dataset;
        if (!trigger || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || typeof HTMLDialogElement === 'undefined' || !HTMLDialogElement.prototype.showModal)
            return;
        event.preventDefault();
        dismiss?.();
        const url = authoring ? new URL('/auth/sign-in', location.origin) : new URL(trigger.href);
        if (authoring) {
            url.searchParams.set('destination', targetURL.pathname);
            const tag = targetURL.searchParams.get('tag');
            if (tag)
                url.searchParams.set('tag', tag);
        }
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
        close.setAttribute('aria-label', labels.close || 'Close');
        close.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg>';
        toolbar.append(close);
        const content = document.createElement('div');
        dialog.append(toolbar, content);
        document.body.append(dialog);
        let disposed = false;
        let closing = false;
        let controller = null;
        let animation = null;
        let timer;
        const cleanup = () => {
            if (disposed)
                return;
            disposed = true;
            controller?.abort();
            clearTimeout(timer);
            animation?.cancel();
            dialog.close();
            dialog.remove();
            delete root.dataset.loginOpen;
            if (trigger.isConnected)
                trigger.focus({ preventScroll: true });
            if (dismiss === cleanup)
                dismiss = null;
        };
        dismiss = cleanup;
        const closeDialog = () => {
            if (closing || disposed)
                return;
            closing = true;
            controller?.abort();
            if (reduced.matches || !dialog.animate) {
                cleanup();
                return;
            }
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
            status.textContent = labels.loading || 'Loading…';
            content.append(status);
            content.setAttribute('aria-busy', 'true');
            try {
                const response = await fetch(url, { signal: request.signal, credentials: 'same-origin', cache: 'no-store' });
                if (!response.ok)
                    throw Error('Sign-in unavailable');
                if (response.headers.get('content-type')?.includes('application/json') && authoring) {
                    const result = await response.json();
                    const next = new URL(result.destination || '', location.origin);
                    if (next.origin !== location.origin || !['/write', '/submit', '/projects'].includes(next.pathname))
                        throw Error('Invalid destination');
                    if (!disposed && !closing && controller === request)
                        location.assign(next.href);
                    return;
                }
                if (!response.headers.get('content-type')?.includes('text/html'))
                    throw Error('Invalid sign-in response');
                const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
                const card = parsed.querySelector('[data-auth-card]');
                if (!card)
                    throw Error('Missing sign-in card');
                if (disposed || closing || controller !== request)
                    return;
                content.className = '';
                content.replaceChildren(document.importNode(card, true));
                dialog.setAttribute('aria-labelledby', 'login-title');
            }
            catch {
                if (disposed || closing || controller !== request)
                    return;
                status.textContent = labels.error || 'Sign-in unavailable';
                status.setAttribute('role', 'alert');
                const actions = document.createElement('div');
                actions.className = 'login-status-actions';
                const retry = document.createElement('button');
                retry.type = 'button';
                retry.className = 'button button-secondary';
                retry.textContent = labels.retry || 'Try again';
                retry.addEventListener('click', () => { close.focus({ preventScroll: true }); void load(); });
                const fallback = document.createElement('a');
                fallback.href = fallbackURL;
                fallback.textContent = labels.fallback || 'Open sign-in page';
                actions.append(retry, fallback);
                content.append(actions);
            }
            finally {
                if (controller === request) {
                    clearTimeout(timer);
                    content.removeAttribute('aria-busy');
                }
            }
        };
        close.addEventListener('click', closeDialog);
        dialog.addEventListener('cancel', event => { event.preventDefault(); closeDialog(); });
        let backdropPress = false;
        dialog.addEventListener('pointerdown', event => { backdropPress = event.target === dialog; });
        dialog.addEventListener('click', event => {
            if (backdropPress && event.target === dialog)
                closeDialog();
            if (event.target.closest('[data-login-dismiss]')) {
                event.preventDefault();
                closeDialog();
            }
        });
        dialog.addEventListener('close', cleanup);
        dialog.addEventListener('submit', event => {
            if (!event.target.matches('[data-login-form]'))
                return;
            try {
                sessionStorage.setItem(pendingKey, JSON.stringify({ url: location.href, y: scrollY, at: Date.now() }));
            }
            catch { /* Authentication works with storage blocked. */ }
        });
        root.dataset.loginOpen = '';
        dialog.showModal();
        close.focus({ preventScroll: true });
        if (!reduced.matches && dialog.animate) {
            animation = dialog.animate([{ opacity: 0, transform: 'translateY(10px) scale(.97)' }, { opacity: 1, transform: 'translateY(0) scale(1)' }], { duration: 300, easing: 'cubic-bezier(.2,.9,.2,1.06)' });
            void animation.finished.catch(() => { });
        }
        void load();
    });
    document.addEventListener('rep0rter:before-language', () => dismiss?.());
    window.addEventListener('pagehide', () => dismiss?.());
})();
// Account editions reuse server translations without discarding an unsent draft.
(() => {
    let request = null;
    document.addEventListener('click', event => {
        if (!document.body.classList.contains('account-page'))
            return;
        const link = event.target?.closest('a[data-language]');
        if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
            return;
        event.preventDefault();
        const url = new URL(link.href);
        if (url.origin !== location.origin)
            return;
        const menu = link.closest('details');
        if (menu)
            menu.open = false;
        request?.abort();
        request = null;
        document.documentElement.removeAttribute('aria-busy');
        if (link.dataset.language === document.documentElement.lang) {
            menu?.querySelector('summary')?.focus({ preventScroll: true });
            return;
        }
        const controller = new AbortController();
        request = controller;
        const timeout = setTimeout(() => controller.abort(), 8000);
        const root = document.documentElement;
        root.setAttribute('aria-busy', 'true');
        const fields = (form) => [...form.querySelectorAll('input[name],textarea[name],select[name]')];
        void (async () => {
            try {
                const response = await fetch(url, { signal: controller.signal, credentials: 'same-origin', cache: 'no-store' });
                if (!response.ok)
                    throw Error('Account edition unavailable');
                const page = new DOMParser().parseFromString(await response.text(), 'text/html');
                if (!page.body.classList.contains('account-page') || page.documentElement.lang !== link.dataset.language)
                    throw Error('Invalid account edition');
                if (controller !== request)
                    return;
                if (document.querySelector('.project-form form') && !page.querySelector('.project-form form'))
                    throw Error('Session changed; keep draft');
                // Capture at commit time, so typing during a slow request is preserved too.
                const forms = [...document.querySelectorAll('main form')];
                const nextForms = [...page.querySelectorAll('main form')];
                forms.forEach((form, index) => {
                    const next = nextForms[index];
                    // Buttons named action shadow form.action; read the URL attribute.
                    if (!next || new URL(form.getAttribute('action') || location.href, location.href).pathname !== new URL(next.getAttribute('action') || url.href, url).pathname)
                        return;
                    const values = fields(form);
                    fields(next).forEach(field => {
                        if (['csrf', 'ui_language'].includes(field.name))
                            return;
                        const previous = values.find(item => item.name === field.name && item.type === field.type);
                        if (!previous)
                            return;
                        field.value = previous.value;
                        if (field instanceof HTMLInputElement && previous instanceof HTMLInputElement)
                            field.checked = previous.checked;
                    });
                });
                const position = { top: scrollY, left: scrollX };
                page.body.querySelectorAll('script').forEach(script => script.remove());
                document.dispatchEvent(new CustomEvent('rep0rter:before-language'));
                document.body.replaceChildren(...[...page.body.childNodes].map(node => document.importNode(node, true)));
                document.body.className = page.body.className;
                document.body.dataset.accountLanguageError = page.body.dataset.accountLanguageError || '';
                root.lang = page.documentElement.lang;
                document.title = page.title;
                history.replaceState(history.state, '', url);
                document.dispatchEvent(new CustomEvent('rep0rter:language-applied'));
                window.scrollTo({ ...position, behavior: 'instant' });
                document.querySelector('.language-trigger')?.focus({ preventScroll: true });
            }
            catch {
                if (controller !== request)
                    return;
                let notice = document.querySelector('[data-account-language-notice]');
                if (!notice) {
                    notice = document.createElement('p');
                    notice.dataset.accountLanguageNotice = '';
                    notice.className = 'form-notice';
                    notice.setAttribute('role', 'alert');
                    document.querySelector('main')?.prepend(notice);
                }
                notice.textContent = document.body.dataset.accountLanguageError || 'Could not change language. Your draft is unchanged.';
            }
            finally {
                clearTimeout(timeout);
                if (controller === request) {
                    request = null;
                    root.removeAttribute('aria-busy');
                }
            }
        })();
    });
    window.addEventListener('pagehide', () => { request?.abort(); request = null; document.documentElement.removeAttribute('aria-busy'); });
})();
