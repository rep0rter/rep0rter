"use strict";
// Same-page editions: generated HTML remains the authoritative translation source.
(() => {
    const codes = { EN: 'en', ZH: 'zh-TW', JA: 'ja', KO: 'ko' };
    const byCode = codes;
    const codeFor = (language) => Object.keys(codes).find(code => codes[code] === language) || 'EN';
    const parseLanguage = (value) => {
        const code = (value || '').toUpperCase();
        return byCode[code] || (code === 'ZH-TW' ? 'zh-TW' : null);
    };
    const preferenceKey = 'rep0rter-language';
    function rememberLanguage(locale) {
        try {
            localStorage.setItem(preferenceKey, locale);
        }
        catch { /* The current language remains usable without storage. */ }
    }
    function deviceLanguage() {
        for (const value of navigator.languages?.length ? navigator.languages : [navigator.language]) {
            const base = (value || '').toLowerCase().split('-')[0];
            if (base === 'zh')
                return 'zh-TW';
            if (base === 'en' || base === 'ja' || base === 'ko')
                return base;
        }
        return 'en';
    }
    const editionPath = /^(.*\/)(?:index(?:\.(en|zh-TW|ja|ko))?\.html)?$/;
    let language = document.documentElement.lang || 'en';
    let revision = 0;
    let controller = null;
    const emit = (name, detail = {}) => document.dispatchEvent(new CustomEvent(name, { detail }));
    function publicURL(value, locale = language) {
        const url = new URL(value, location.href);
        const match = url.pathname.match(editionPath);
        if (url.origin !== location.origin || !match)
            return url;
        url.pathname = `${match[1]}index.html`;
        url.searchParams.set('lang', codeFor(locale));
        return url;
    }
    function sourceURL(locale) {
        const url = new URL(location.href);
        const match = url.pathname.match(editionPath);
        if (!match)
            throw Error('Unsupported edition path');
        url.pathname = `${match[1]}index${locale === 'en' ? '' : `.${locale}`}.html`;
        url.search = '';
        url.hash = '';
        return url;
    }
    function updateLinks() {
        document.querySelectorAll('a[href]').forEach(link => {
            if (link.hasAttribute('download') || link.getAttribute('href').startsWith('#'))
                return;
            const locale = link.dataset.language || language;
            const url = publicURL(link.href, locale);
            if (link.dataset.language) {
                url.search = location.search;
                url.searchParams.set('lang', codeFor(locale));
                url.hash = location.hash;
                if (locale === language)
                    link.setAttribute('aria-current', 'page');
                else
                    link.removeAttribute('aria-current');
            }
            link.href = url.href;
        });
        // Detail-page searches return to the same edition of the homepage.
        document.querySelectorAll('form[role="search"]').forEach(form => {
            form.action = publicURL(form.action).href;
            let field = form.querySelector('input[name="lang"]');
            if (!field) {
                field = document.createElement('input');
                field.type = 'hidden';
                field.name = 'lang';
                form.append(field);
            }
            field.value = codeFor(language);
        });
    }
    function refreshLanguageLinks() {
        document.querySelectorAll('a[data-language]').forEach(link => {
            const url = publicURL(location.href, link.dataset.language);
            link.href = url.href;
        });
    }
    function closeMenu(focus = true) {
        const menu = document.querySelector('.language-menu');
        if (!menu)
            return;
        menu.open = false;
        if (focus)
            menu.querySelector('summary')?.focus({ preventScroll: true });
    }
    function notice(message = '') {
        let status = document.querySelector('[data-language-status]');
        if (!status && message) {
            status = document.createElement('p');
            status.dataset.languageStatus = '';
            status.className = 'language-status';
            status.setAttribute('role', 'status');
            document.body.append(status);
        }
        if (status) {
            status.textContent = message;
            status.hidden = !message;
        }
    }
    const messages = {
        en: ['Loading language…', 'Could not load this language. Your current page is unchanged. Try again.'],
        'zh-TW': ['正在切換語言…', '目前無法載入此語言，原頁面已保留。請稍後再試。'],
        ja: ['言語を読み込み中…', '言語を読み込めませんでした。現在のページは保持されています。もう一度お試しください。'],
        ko: ['언어를 불러오는 중…', '언어를 불러오지 못했습니다. 현재 페이지는 유지됩니다. 다시 시도해 주세요.'],
    };
    function key(element) {
        if (element.id)
            return `#${CSS.escape(element.id)}`;
        const article = element.closest('article[id]');
        const preview = element.closest('[data-preview-post-id]');
        const container = article || preview || document.body;
        const path = [];
        for (let node = element; node && node !== container; node = node.parentElement) {
            path.unshift(`${node.localName}:nth-child(${[...node.parentElement.children].indexOf(node) + 1})`);
        }
        const prefix = article ? `#${CSS.escape(article.id)}` : preview
            ? `[data-preview-post-id="${CSS.escape(preview.dataset.previewPostId)}"]` : 'body';
        return `${prefix} > ${path.join(' > ')}`;
    }
    function capture() {
        const top = Math.max(0, document.querySelector('.site-header')?.getBoundingClientRect().bottom || 0);
        const candidates = [...document.querySelectorAll('main h1, main h2, article h3, article .summary, article blockquote, main .hero-intro, .latest-item h3, .latest-summary, footer p')]
            .filter(element => { const r = element.getBoundingClientRect(); return r.width && r.height && r.bottom > top && r.top < innerHeight; });
        const anchor = candidates.sort((a, b) => Math.abs(a.getBoundingClientRect().top - top) - Math.abs(b.getBoundingClientRect().top - top))[0];
        return {
            x: scrollX, y: scrollY,
            anchor: scrollY > 0 && anchor ? key(anchor) : null,
            top: anchor?.getBoundingClientRect().top,
            details: [...document.querySelectorAll('main details')]
                .map((element) => [key(element), element.open]),
            query: document.querySelector('#story-search')?.value,
        };
    }
    function updateHead(next, locale) {
        document.title = next.title;
        ['meta[name="description"]', 'meta[property="og:title"]', 'meta[property="og:description"]',
            'meta[property="og:image"]', 'meta[property="og:url"]', 'link[rel="canonical"]',
            'link[rel="alternate"][type="application/rss+xml"]'].forEach(selector => {
            const source = next.querySelector(selector);
            const current = document.head.querySelector(selector);
            if (!source || !current)
                return;
            const name = source.tagName === 'LINK' ? 'href' : 'content';
            let value = source.getAttribute(name) || '';
            if (selector.includes('canonical') || selector.includes('og:url'))
                value = publicURL(value, locale).href;
            current.setAttribute(name, value);
        });
    }
    function restore(position) {
        position.details.forEach(([selector, open]) => {
            const element = document.querySelector(selector);
            if (element?.tagName === 'DETAILS')
                element.open = open;
        });
        let anchor = position.anchor && document.querySelector(position.anchor);
        if (anchor && !anchor.getClientRects().length)
            anchor = null;
        const offset = anchor ? anchor.getBoundingClientRect().top - position.top : 0;
        window.scrollTo({ left: position.x, top: Math.max(0, anchor ? scrollY + offset : position.y), behavior: 'instant' });
    }
    async function change(locale, { initial = false, history = true } = {}) {
        if (!locale || !Object.values(codes).includes(locale))
            return;
        const token = ++revision;
        controller?.abort();
        controller = null;
        if (locale === language) {
            if (!initial && history)
                rememberLanguage(locale);
            if (history)
                window.history.replaceState(window.history.state, '', publicURL(location.href, locale));
            closeMenu(!initial);
            notice();
            document.documentElement.removeAttribute('aria-busy');
            return;
        }
        closeMenu(!initial);
        const request = new AbortController();
        controller = request;
        notice(messages[language]?.[0] || messages.en[0]);
        document.documentElement.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(sourceURL(locale), { signal: request.signal, cache: 'no-cache', headers: { Accept: 'text/html' } });
            if (!response.ok)
                throw Error(`Language response ${response.status}`);
            const next = new DOMParser().parseFromString(await response.text(), 'text/html');
            if (token !== revision)
                return;
            if (next.documentElement.lang !== locale || !next.querySelector('main'))
                throw Error('Invalid language page');
            // Capture at commit time: scrolling while a slow request runs is respected.
            const position = capture();
            emit('rep0rter:before-language');
            const root = document.documentElement;
            root.dataset.languageUpdating = '';
            try {
                next.body.querySelectorAll('script').forEach(script => script.remove());
                document.body.replaceChildren(...[...next.body.childNodes].map(node => document.importNode(node, true)));
                document.body.className = next.body.className;
                for (const key of ['brandSkip', 'brandDismiss', 'brandIntro']) {
                    document.body.dataset[key] = next.body.dataset[key] || '';
                }
                language = locale;
                if (!initial && history)
                    rememberLanguage(locale);
                root.lang = locale;
                if (history)
                    window.history.replaceState(window.history.state, '', publicURL(location.href, locale));
                updateHead(next, locale);
                const search = document.querySelector('#story-search');
                if (search && position.query !== undefined)
                    search.value = position.query;
                updateLinks();
                emit('rep0rter:language-applied', { language: locale, animate: false });
                restore(position);
                if (!initial)
                    document.querySelector('.language-trigger')?.focus({ preventScroll: true });
                notice();
            }
            finally {
                delete root.dataset.languageUpdating;
                root.removeAttribute('aria-busy');
            }
            // Decoration starts after layout/viewport are restored, never during a swap.
            requestAnimationFrame(() => {
                if (token === revision)
                    emit('rep0rter:language-settled', { language: locale, animate: !initial });
            });
        }
        catch (error) {
            if (token !== revision || (error instanceof Error && error.name === 'AbortError'))
                return;
            document.documentElement.removeAttribute('aria-busy');
            notice(messages[language]?.[1] || messages.en[1]);
            if (initial)
                window.history.replaceState(window.history.state, '', publicURL(location.href, language));
        }
        finally {
            if (token === revision)
                controller = null;
        }
    }
    function initialize() {
        const url = new URL(location.href);
        let requested = parseLanguage(url.searchParams.get('lang'));
        // Explicit edition links always win. Only the neutral homepage negotiates
        // a language, so article links and the presentation keep their own edition.
        if (!requested && document.body.classList.contains('page-home')) {
            const edition = url.pathname.match(editionPath)?.[2];
            if (edition)
                requested = parseLanguage(edition);
            else {
                try {
                    requested = parseLanguage(localStorage.getItem(preferenceKey));
                }
                catch { /* Device language and the English fallback need no storage. */ }
                requested ||= deviceLanguage();
            }
        }
        const initialLanguage = requested || language;
        if (initialLanguage === language)
            window.history.replaceState(window.history.state, '', publicURL(location.href, language));
        updateLinks();
        if (initialLanguage !== language)
            change(initialLanguage, { initial: true });
    }
    for (const name of ['pointerdown', 'focusin'])
        document.addEventListener(name, event => {
            if (event.target instanceof Element && event.target.closest('a[data-language]'))
                refreshLanguageLinks();
        });
    document.addEventListener('click', event => {
        const link = event.target instanceof Element
            ? event.target.closest('a[data-language]') : null;
        if (link)
            refreshLanguageLinks();
        if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey ||
            event.shiftKey || event.altKey || (link.target && link.target !== '_self') || link.hasAttribute('download'))
            return;
        event.preventDefault();
        change(link.dataset.language);
    });
    window.addEventListener('popstate', () => {
        const requested = parseLanguage(new URL(location.href).searchParams.get('lang')) || language;
        change(requested, { history: false });
    });
    window.addEventListener('pagehide', () => { revision += 1; controller?.abort(); });
    window.Rep0rterLanguage = Object.freeze({ change, publicURL });
    if (document.readyState === 'loading')
        document.addEventListener('DOMContentLoaded', initialize, { once: true });
    else
        initialize();
})();
