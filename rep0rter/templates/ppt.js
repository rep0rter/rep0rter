"use strict";
// A finite, keyboard-first presentation; no global framework or motion dependency.
(() => {
    const stage = document.querySelector('.ppt-stage');
    const slides = Array.from(document.querySelectorAll('.ppt-slide'));
    if (!stage || !slides.length)
        return;
    const dots = Array.from(document.querySelectorAll('[data-slide-to]'));
    const previous = document.querySelector('[data-slide-prev]');
    const next = document.querySelector('[data-slide-next]');
    const counter = document.querySelector('[data-slide-number]');
    const status = document.querySelector('[data-slide-status]');
    let current = -1;
    const fromHash = () => {
        const match = /^#slide-(\d+)$/.exec(location.hash);
        return match ? Math.min(slides.length - 1, Math.max(0, Number(match[1]) - 1)) : 0;
    };
    const demoSlide = slides[2];
    const frame = demoSlide?.querySelector('iframe[data-src]');
    const browser = frame?.closest('.ppt-browser');
    const demoLabels = ['Latest updates', 'Search & filters', 'Read in Japanese', 'Light → dark'];
    const demoCaptions = [
        'The actual community timeline, with original sources one click away.',
        'The real filter panel, using a source already present in the timeline.',
        'The same reader in Japanese, using the published translated edition.',
        'The actual dark appearance. Your saved theme preference stays unchanged.',
    ];
    let demoStep = 0;
    let loadedEdition = '';
    let requestedEdition = '';
    let revealTimer = 0;
    const prepareEmbeddedNavigation = () => {
        try {
            const doc = frame?.contentDocument;
            if (!doc?.head || doc.getElementById('ppt-embedded-navigation'))
                return;
            const style = doc.createElement('style');
            style.id = 'ppt-embedded-navigation';
            // The presentation owns the crossfade. Native iframe navigation snapshots
            // can race when arrow presses replace a still-running navigation.
            style.textContent = '@view-transition { navigation: none; }';
            doc.head.append(style);
            // Commit the opt-out before assigning the next iframe URL.
            void doc.documentElement.offsetWidth;
        }
        catch { /* Only same-origin reader documents can be prepared. */ }
    };
    const demoURL = (edition) => {
        const url = new URL(frame?.dataset.src || location.href, location.href);
        url.pathname = url.pathname.replace(/index(?:\.[^.\/]+)?\.html$/, edition === 'ja' ? 'index.ja.html' : 'index.html');
        url.search = edition === 'ja' ? '?lang=JA' : '?lang=EN';
        return url.href;
    };
    const announce = () => {
        if (status)
            status.textContent = `Slide ${current + 1} of ${slides.length}: ${slides[current]?.dataset.label || ''}${current === 2 ? `. Demo ${demoStep + 1} of 4: ${demoLabels[demoStep]}` : ''}`;
        previous?.setAttribute('aria-label', current === 2 && demoStep > 0 ? 'Previous demo view' : 'Previous slide');
        next?.setAttribute('aria-label', current === 2 && demoStep < 3 ? 'Next demo view' : 'Next slide');
    };
    const applyDemo = () => {
        if (!frame || !demoSlide || current !== 2)
            return;
        const edition = demoStep >= 2 ? 'ja' : 'en';
        demoSlide.dataset.demoStep = String(demoStep);
        const label = demoSlide.querySelector('[data-demo-label]');
        const count = demoSlide.querySelector('[data-demo-count]');
        const caption = demoSlide.querySelector('[data-demo-caption]');
        if (label)
            label.textContent = demoLabels[demoStep] || '';
        if (count)
            count.textContent = `${String(demoStep + 1).padStart(2, '0')} / 04`;
        if (caption)
            caption.textContent = demoCaptions[demoStep] || '';
        clearTimeout(revealTimer);
        browser?.classList.remove('is-loaded');
        browser?.setAttribute('aria-busy', 'true');
        if (edition !== loadedEdition || edition !== requestedEdition) {
            if (edition !== requestedEdition) {
                requestedEdition = edition;
                prepareEmbeddedNavigation();
                frame.src = demoURL(edition);
            }
            return;
        }
        try {
            const doc = frame.contentDocument;
            const win = frame.contentWindow;
            if (!doc || !win)
                return;
            // Change only this embedded document, never the reader's stored preference.
            doc.documentElement.dataset.theme = demoStep === 3 ? 'dark' : 'light';
            void win.Rep0rterImages?.sync();
            const filters = doc.querySelector('[data-filters]');
            if (filters)
                filters.open = demoStep === 1;
            const source = doc.querySelector('[data-filter="source"]');
            if (source) {
                source.value = demoStep === 1 ? (Array.from(source.options).find(option => !!option.value)?.value || '') : '';
                const change = doc.createEvent('Event');
                change.initEvent('change', true, false);
                source.dispatchEvent(change);
            }
            const anchor = demoStep === 1 ? filters : doc.querySelector('#news');
            if (anchor)
                win.scrollTo({ top: Math.max(0, anchor.getBoundingClientRect().top + win.scrollY - 130), behavior: 'instant' });
            revealTimer = window.setTimeout(() => {
                if (current !== 2)
                    return;
                browser?.classList.add('is-loaded');
                browser?.setAttribute('aria-busy', 'false');
            }, window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 120);
        }
        catch {
            const loading = demoSlide.querySelector('.ppt-preview-loading');
            if (loading)
                loading.textContent = 'The live preview is unavailable. Continue with →.';
            browser?.setAttribute('aria-busy', 'false');
        }
    };
    frame?.addEventListener('load', () => {
        try {
            loadedEdition = frame.contentDocument?.documentElement.lang || '';
        }
        catch {
            loadedEdition = '';
        }
        prepareEmbeddedNavigation();
        if (current !== 2)
            return;
        // An earlier navigation may finish after another key press: latest state wins.
        const desired = demoStep >= 2 ? 'ja' : 'en';
        if (loadedEdition !== desired) {
            // A stale load must not restart or overwrite the newer navigation.
            // If the requested edition itself failed, leave a useful status instead.
            try {
                if (frame.contentWindow?.location.pathname === new URL(demoURL(desired)).pathname) {
                    const loading = demoSlide?.querySelector('.ppt-preview-loading');
                    if (loading)
                        loading.textContent = 'This edition is unavailable. Continue with →.';
                    browser?.setAttribute('aria-busy', 'false');
                }
            }
            catch { /* A blocked frame remains an optional visual preview. */ }
            return;
        }
        applyDemo();
    });
    const go = (requested, updateHash = true, entryStep = 0) => {
        const index = Math.min(slides.length - 1, Math.max(0, requested));
        if (index === current && (index !== 2 || demoStep === entryStep))
            return;
        const oldSlide = slides[current];
        const moveFocus = !!oldSlide?.contains(document.activeElement);
        document.documentElement.style.setProperty('--ppt-direction', index > current ? '1' : '-1');
        current = index;
        if (current === 2)
            demoStep = entryStep;
        slides.forEach((slide, position) => {
            const active = position === current;
            slide.classList.toggle('is-active', active);
            slide.classList.toggle('is-before', position < current);
            slide.setAttribute('aria-hidden', String(!active));
            slide.inert = !active;
            if (active) {
                slide.scrollTop = 0;
                if (position === 2)
                    applyDemo();
            }
        });
        dots.forEach((dot, position) => {
            if (position === current)
                dot.setAttribute('aria-current', 'step');
            else
                dot.removeAttribute('aria-current');
        });
        if (previous)
            previous.disabled = current === 0;
        if (next)
            next.disabled = current === slides.length - 1;
        if (counter)
            counter.textContent = String(current + 1).padStart(2, '0');
        document.documentElement.style.setProperty('--ppt-progress', `${((current + 1) / slides.length) * 100}%`);
        announce();
        if (updateHash)
            history.replaceState(null, '', `#slide-${current + 1}`);
        if (moveFocus)
            stage.focus({ preventScroll: true });
    };
    const advance = (direction) => {
        if (current === 2 && demoStep + direction >= 0 && demoStep + direction <= 3) {
            demoStep += direction;
            applyDemo();
            announce();
            return;
        }
        go(current + direction, true, current + direction === 2 && direction < 0 ? 3 : 0);
    };
    document.documentElement.dataset.presentation = 'ready';
    go(fromHash(), false);
    previous?.addEventListener('click', () => advance(-1));
    next?.addEventListener('click', () => advance(1));
    dots.forEach(dot => dot.addEventListener('click', () => go(Number(dot.dataset.slideTo))));
    window.addEventListener('hashchange', () => go(fromHash(), false));
    document.addEventListener('keydown', event => {
        if (event.altKey || event.ctrlKey || event.metaKey || event.defaultPrevented)
            return;
        const target = event.target;
        if (target?.closest('input, textarea, select, [contenteditable="true"]'))
            return;
        let destination;
        switch (event.key) {
            case 'ArrowRight':
            case 'PageDown':
                event.preventDefault();
                advance(1);
                return;
            case 'ArrowLeft':
            case 'PageUp':
                event.preventDefault();
                advance(-1);
                return;
            case 'Home':
                destination = 0;
                break;
            case 'End':
                destination = slides.length - 1;
                break;
            default: return;
        }
        event.preventDefault();
        go(destination);
    });
    // Horizontal swipes navigate while vertical gestures retain slide scrolling.
    let touchStart = null;
    stage.addEventListener('touchstart', event => {
        const touch = event.touches[0];
        touchStart = event.touches.length === 1 && touch ? { x: touch.clientX, y: touch.clientY } : null;
    }, { passive: true });
    stage.addEventListener('touchend', event => {
        const touch = event.changedTouches[0];
        if (!touch || !touchStart)
            return;
        const dx = touch.clientX - touchStart.x;
        const dy = touch.clientY - touchStart.y;
        touchStart = null;
        if (Math.abs(dx) > 65 && Math.abs(dx) > Math.abs(dy) * 1.6)
            advance(dx < 0 ? 1 : -1);
    }, { passive: true });
    stage.addEventListener('touchcancel', () => { touchStart = null; }, { passive: true });
})();
