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
            if (!event.target.matches('[data-login-form], [data-logout-form]'))
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
// Form feedback: in-flight buttons, field-level server errors, publish
// confirmation on arrival, and a local signed-in hint for the reader header.
(() => {
    const publishedKey = 'rep0rter-just-published';
    const accountKey = 'rep0rter-signed-in';
    const store = (action) => {
        try {
            action(sessionStorage);
        }
        catch { /* Feedback is optional when storage is blocked. */ }
    };
    // Server messages are English-only; map each to the field that caused it.
    const FIELD_ERRORS = [
        [/^Headline|^Project name|^The headline template/, 'title'],
        [/^What happened|^Description/, 'description'],
        [/^Public display name/, 'author'],
        [/^How to take part/, 'take_part'],
        [/^Original text/, 'original'],
        [/evidence link|^Evidence links/i, 'evidence'],
        [/hashtag/i, 'hashtags'],
        [/^Choose the date/, 'event_date'],
        [/^Enter a valid public project URL/, 'url'],
        [/^Enter a GitHub repository|^Enter a public HTTPS RSS/, 'source_url'],
        [/^The summary template|^Templates support/, 'summary_template'],
        [/^Confirm that/, 'owner_confirmed'],
    ];
    const markFieldError = () => {
        const notice = document.querySelector('.account-page main > .form-notice[role=alert]');
        const form = document.querySelector('.project-form form');
        if (!notice || !form || form.querySelector('.field-error'))
            return;
        const message = (notice.querySelector('p') || notice).textContent?.trim() || '';
        const name = FIELD_ERRORS.find(([pattern]) => pattern.test(message))?.[1];
        const field = name ? form.querySelector(`[name="${name}"]`) : null;
        if (!field)
            return;
        const anchor = field.closest('.owner-confirmation') || field;
        const inline = document.createElement('p');
        inline.className = 'field-error';
        inline.id = `${name}-error`;
        inline.textContent = message;
        anchor.after(inline);
        field.setAttribute('aria-invalid', 'true');
        field.setAttribute('aria-describedby', [field.getAttribute('aria-describedby'), inline.id].filter(Boolean).join(' '));
        field.addEventListener('input', () => { field.removeAttribute('aria-invalid'); inline.remove(); }, { once: true });
        // The banner sits above the form; link it to the field it is about.
        const label = field.id ? form.querySelector(`label[for="${field.id}"]`)?.firstChild?.textContent?.trim() : '';
        if (label) {
            const jump = document.createElement('a');
            jump.className = 'field-link';
            jump.href = `#${field.id}`;
            jump.textContent = `↓ ${label}`;
            jump.addEventListener('click', event => {
                event.preventDefault();
                field.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
                field.focus({ preventScroll: true });
            });
            notice.append(document.createElement('br'), jump);
        }
    };
    const formButtons = (form) => Array.from(form.elements).filter((element) => element instanceof HTMLButtonElement);
    // The preview's Edit link moves to the first field and puts the cursor there.
    document.addEventListener('click', event => {
        const link = event.target?.closest('a[data-edit-story]');
        const field = link ? document.getElementById(decodeURIComponent(new URL(link.href).hash.slice(1))) : null;
        if (!field || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
            return;
        event.preventDefault();
        field.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
        field.focus({ preventScroll: true });
    });
    const accountPage = () => {
        if (!document.body.classList.contains('account-page'))
            return;
        // Arriving on a form again means the last attempt did not publish.
        store(storage => storage.removeItem(publishedKey));
        const signedIn = Boolean(document.querySelector('.sign-in-link[data-signed-in], .login-identity'));
        try {
            if (signedIn)
                localStorage.setItem(accountKey, '1');
            else if (document.querySelector('[data-login-form]'))
                localStorage.removeItem(accountKey);
        }
        catch { /* The header hint is cosmetic. */ }
        markFieldError();
    };
    document.addEventListener('submit', event => {
        const form = event.target;
        // A button named "action" shadows form.action; read the attribute instead.
        const target = new URL(form.getAttribute('action') || location.href, location.href).pathname;
        if (form.matches('[data-logout-form]') || target === '/auth/logout') {
            try {
                localStorage.removeItem(accountKey);
            }
            catch { /* cosmetic */ }
        }
        if (!form.closest('.project-form') || event.defaultPrevented)
            return;
        const submitter = event.submitter;
        const action = submitter?.value || 'publish';
        if (action === 'publish' && /\/(write|submit)$/.test(target)) {
            store(storage => storage.setItem(publishedKey, JSON.stringify({
                kind: target.endsWith('/submit') ? 'project' : 'story', at: Date.now(),
            })));
        }
        // Disable after the browser has captured the submitter's name and value.
        // form.elements includes buttons outside the form tied to it by form="…".
        setTimeout(() => {
            form.setAttribute('aria-busy', 'true');
            formButtons(form).forEach(button => { button.disabled = true; });
            const label = submitter?.dataset.busyLabel;
            if (submitter && label) {
                submitter.dataset.busy = '';
                const spinner = document.createElement('span');
                spinner.className = 'busy-spinner';
                spinner.setAttribute('aria-hidden', 'true');
                submitter.replaceChildren(spinner, document.createTextNode(label));
            }
        }, 0);
    });
    // Restore usable buttons when the browser returns this page from its cache.
    window.addEventListener('pageshow', event => {
        if (!event.persisted)
            return;
        document.querySelectorAll('form[aria-busy="true"]').forEach(form => {
            form.removeAttribute('aria-busy');
            formButtons(form).forEach(button => { button.disabled = false; });
        });
    });
    const readerPage = () => {
        if (document.body.classList.contains('account-page'))
            return;
        try {
            const entry = document.querySelector('[data-sign-in]');
            if (entry && localStorage.getItem(accountKey) === '1' && entry.dataset.accountLabel) {
                entry.dataset.signedIn = '';
                entry.setAttribute('aria-label', entry.dataset.accountLabel);
                entry.title = entry.dataset.accountLabel;
                const text = entry.querySelector('.masthead-link-label');
                if (text)
                    text.textContent = entry.dataset.accountLabel;
            }
        }
        catch { /* cosmetic */ }
        if (!document.body.classList.contains('page-story'))
            return;
        let notice = null;
        store(storage => {
            const raw = storage.getItem(publishedKey);
            storage.removeItem(publishedKey);
            notice = raw ? JSON.parse(raw) : null;
        });
        const published = notice;
        const copy = document.body.dataset;
        if (!published || !published.at || Date.now() - published.at > 600_000 || !copy.publishedStory)
            return;
        const banner = document.createElement('div');
        banner.className = 'publish-banner';
        banner.setAttribute('role', 'status');
        banner.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="m5 12 4 4L19 6"/></svg>';
        const title = document.createElement('strong');
        title.textContent = (published.kind === 'project' ? copy.publishedProject : copy.publishedStory) || '';
        const body = document.createElement('p');
        body.textContent = copy.publishedBody || '';
        const links = document.createElement('nav');
        const again = published.kind === 'project'
            ? [copy.publishedProjectAgain, copy.publishedProjectAgainLabel] : [copy.publishedWrite, copy.publishedWriteLabel];
        for (const [href, label] of [[copy.publishedFeed, copy.publishedFeedLabel], again]) {
            if (!href || !label)
                continue;
            const link = document.createElement('a');
            link.href = href;
            link.textContent = label;
            links.append(link);
        }
        banner.append(title, body, links);
        document.querySelector('main .detail-intro, main .page-intro')?.after(banner);
    };
    const ready = () => { accountPage(); readerPage(); };
    if (document.readyState === 'loading')
        document.addEventListener('DOMContentLoaded', ready, { once: true });
    else
        ready();
    document.addEventListener('rep0rter:language-applied', markFieldError);
})();
