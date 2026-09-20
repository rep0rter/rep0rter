"use strict";
// Progressive entrance and disclosure motion; native content remains the fallback.
(() => {
    const root = document.documentElement;
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    let cancelEntry = () => { };
    const prepareEntry = () => {
        if (!root.hasAttribute('data-brand-entry') || reduced.matches)
            return;
        try {
            if (localStorage.getItem('rep0rter-brand-dismissed') === '1')
                return;
        }
        catch { /* Persistent preferences are optional. */ }
        const navigation = performance.getEntriesByType('navigation')[0];
        const url = new URL(location.href);
        if ((navigation && navigation.type !== 'navigate') || location.hash || scrollY > 0 ||
            [...url.searchParams.keys()].some(key => key !== 'lang'))
            return;
        try {
            if (sessionStorage.getItem('rep0rter-brand-seen') || sessionStorage.getItem('rep0rter-login-return'))
                return;
            sessionStorage.setItem('rep0rter-brand-seen', '1');
        }
        catch { /* Storage is optional; at most one entrance per document. */ }
        root.dataset.brandEntering = 'pending';
        const release = window.Rep0rterScrollLock?.acquire() || (() => { });
        let layer = null;
        let controls = null;
        let previousFocus = null;
        let frame = 0;
        let hold = 0;
        let exitTimer = 0;
        let leaving = false;
        let ended = false;
        let original = null;
        let previousInert = false;
        let madeInert = false;
        const finish = () => {
            if (ended)
                return;
            ended = true;
            clearTimeout(failsafe);
            clearTimeout(hold);
            clearTimeout(exitTimer);
            cancelAnimationFrame(frame);
            const restoreFocus = controls?.contains(document.activeElement);
            controls?.close();
            controls?.remove();
            layer?.remove();
            original?.removeAttribute('data-brand-original');
            delete root.dataset.brandEntering;
            delete root.dataset.brandExiting;
            root.style.removeProperty('--brand-veil-opacity');
            if (madeInert)
                document.body.inert = previousInert;
            release();
            if (restoreFocus) {
                const target = previousFocus?.isConnected && previousFocus !== document.body
                    ? previousFocus : original || document.querySelector('.masthead .wordmark');
                target?.focus({ preventScroll: true });
            }
            window.removeEventListener('resize', finish);
            window.removeEventListener('pagehide', finish);
            document.removeEventListener('keydown', escape);
            document.removeEventListener('rep0rter:before-language', beforeLanguage);
            reduced.removeEventListener('change', motionPreference);
        };
        const fadeControls = () => {
            if (!controls || controls.dataset.leaving)
                return;
            if (controls.contains(document.activeElement))
                controls.focus({ preventScroll: true });
            controls.querySelectorAll('button').forEach(button => { button.disabled = true; });
            controls.dataset.leaving = 'true';
        };
        const leave = () => {
            if (ended || leaving)
                return;
            if (reduced.matches || !controls?.isConnected) {
                finish();
                return;
            }
            leaving = true;
            clearTimeout(hold);
            cancelAnimationFrame(frame);
            fadeControls();
            root.dataset.brandExiting = '';
            root.style.setProperty('--brand-veil-opacity', '0');
            if (layer)
                layer.style.opacity = '0';
            // Let the 240ms opacity transition paint its last frame before cleanup.
            exitTimer = setTimeout(finish, 280);
        };
        const escape = (event) => { if (event.key === 'Escape')
            leave(); };
        const beforeLanguage = () => { if (layer)
            finish(); };
        const motionPreference = () => { if (reduced.matches)
            finish(); };
        const failsafe = setTimeout(finish, 4500);
        cancelEntry = finish;
        window.addEventListener('resize', finish);
        window.addEventListener('pagehide', finish);
        document.addEventListener('keydown', escape);
        document.addEventListener('rep0rter:before-language', beforeLanguage);
        reduced.addEventListener('change', motionPreference);
        const start = () => {
            if (ended || leaving)
                return;
            if (!document.body.classList.contains('page-home') || scrollY > 0) {
                finish();
                return;
            }
            previousInert = document.body.inert;
            madeInert = true;
            document.body.inert = true;
            // A modal dialog escapes the body's inert state, keeping these two actions
            // keyboard-accessible while the reading interface remains locked.
            controls = document.createElement('dialog');
            controls.className = 'brand-entry-controls';
            controls.tabIndex = -1;
            const skip = document.createElement('button');
            skip.type = 'button';
            skip.className = 'brand-entry-skip';
            skip.addEventListener('click', leave);
            const dismiss = document.createElement('button');
            dismiss.type = 'button';
            dismiss.className = 'brand-entry-dismiss';
            dismiss.addEventListener('click', () => {
                try {
                    localStorage.setItem('rep0rter-brand-dismissed', '1');
                }
                catch { /* The current entrance can still be dismissed without storage. */ }
                leave();
            });
            const labelControls = () => {
                controls.setAttribute('aria-label', document.body.dataset.brandIntro || 'Brand introduction');
                skip.textContent = document.body.dataset.brandSkip || 'Skip';
                dismiss.textContent = document.body.dataset.brandDismiss || 'Don’t show again';
            };
            labelControls();
            controls.append(skip, dismiss);
            controls.addEventListener('cancel', event => { event.preventDefault(); leave(); });
            previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
            document.body.append(controls);
            try {
                controls.showModal();
            }
            catch {
                finish();
                return;
            }
            const ready = () => {
                if (ended || leaving)
                    return;
                // The locale loader may replace the body after DOMContentLoaded.
                if (root.getAttribute('aria-busy') === 'true') {
                    frame = requestAnimationFrame(ready);
                    return;
                }
                try {
                    // Initial language selection can replace the entire body. Reattach
                    // the controls to that body and use its actual translated labels.
                    if (!controls.isConnected) {
                        document.body.append(controls);
                        controls.close();
                        controls.showModal();
                        document.body.inert = true;
                    }
                    labelControls();
                    original = document.querySelector('.masthead .wordmark');
                    if (!original) {
                        finish();
                        return;
                    }
                    const box = original.getBoundingClientRect();
                    if (box.width <= 0 || box.height <= 0 || box.top < 0) {
                        finish();
                        return;
                    }
                    layer = original.cloneNode(true);
                    layer.removeAttribute('href');
                    layer.removeAttribute('id');
                    layer.querySelectorAll('[id]').forEach(element => element.removeAttribute('id'));
                    layer.setAttribute('aria-hidden', 'true');
                    layer.setAttribute('tabindex', '-1');
                    layer.classList.add('brand-entry-clone');
                    const face = document.createElement('span');
                    face.className = 'brand-entry-face';
                    face.append(...Array.from(layer.childNodes));
                    layer.append(face);
                    // Grid tracks may stretch the anchor far beyond its visible brand.
                    // Animate the actual mark, not the empty width of that grid cell.
                    layer.style.width = 'max-content';
                    layer.style.height = `${box.height}px`;
                    layer.style.left = `${box.left}px`;
                    layer.style.top = `${box.top}px`;
                    // Copy inherited typography: the clone is outside its masthead context.
                    const typography = getComputedStyle(original);
                    layer.style.fontSize = typography.fontSize;
                    layer.style.gap = typography.gap;
                    original.setAttribute('data-brand-original', '');
                    document.body.append(layer);
                    root.dataset.brandEntering = 'active';
                    const visibleWidth = layer.getBoundingClientRect().width;
                    // Include the optical surface's 18px gutters in the mobile safe area.
                    const scale = Math.max(1, Math.min(3.6, (root.clientWidth - 48) / (visibleWidth + 36)));
                    const x = (root.clientWidth - visibleWidth * scale) / 2 - box.left;
                    const y = (innerHeight - box.height * scale) / 2 - box.top;
                    const paint = (value) => {
                        if (!layer || !original?.isConnected) {
                            finish();
                            return;
                        }
                        const remaining = Math.max(0, Math.min(1, 1 - value));
                        layer.style.transform = `translate(${x * remaining}px, ${y * remaining}px) scale(${1 + (scale - 1) * remaining})`;
                        root.style.setProperty('--brand-veil-opacity', String(remaining));
                    };
                    paint(0);
                    layer.dataset.phase = 'reveal';
                    hold = setTimeout(() => {
                        if (ended)
                            return;
                        layer.dataset.phase = 'hold';
                        hold = setTimeout(() => {
                            if (ended)
                                return;
                            layer.dataset.phase = 'dock';
                            fadeControls();
                            // A critically damped spring gives the longer journey a quiet,
                            // continuous arrival, without changing other interface springs.
                            const started = performance.now();
                            const settle = 1 - 10 * Math.exp(-9);
                            const dock = (now) => {
                                if (ended)
                                    return;
                                const time = Math.min(1, (now - started) / 850);
                                paint((1 - (1 + 9 * time) * Math.exp(-9 * time)) / settle);
                                if (time === 1)
                                    finish();
                                else
                                    frame = requestAnimationFrame(dock);
                            };
                            frame = requestAnimationFrame(dock);
                        }, 1200);
                    }, 650);
                }
                catch {
                    finish();
                }
            };
            // Give deferred language initialization and fonts a bounded chance to settle.
            void Promise.race([document.fonts.ready, new Promise(resolve => setTimeout(resolve, 250))])
                .then(() => { if (!ended && !leaving)
                frame = requestAnimationFrame(ready); }, finish);
        };
        if (document.readyState === 'loading')
            document.addEventListener('DOMContentLoaded', start, { once: true });
        else
            start();
    };
    prepareEntry();
    let disposeFilters = () => { };
    const initializeFilters = () => {
        disposeFilters();
        const details = document.querySelector('[data-filters]');
        const summary = details?.querySelector('summary');
        const panel = details?.querySelector('.filter-panel');
        if (!details || !summary || !panel)
            return;
        let expanded = details.open;
        let motion = null;
        let velocity = 0;
        let height = details.getBoundingClientRect().height;
        let target = height;
        let timeout = 0;
        const naturalHeight = () => summary.getBoundingClientRect().height +
            (expanded ? panel.getBoundingClientRect().height : 0) +
            parseFloat(getComputedStyle(details).borderTopWidth) + parseFloat(getComputedStyle(details).borderBottomWidth);
        const clearStyles = () => {
            details.style.removeProperty('height');
            details.removeAttribute('data-filter-moving');
            panel.style.removeProperty('opacity');
            panel.inert = false;
        };
        const settle = () => {
            clearTimeout(timeout);
            motion?.cancel();
            motion = null;
            velocity = 0;
            details.open = expanded;
            clearStyles();
            summary.setAttribute('aria-expanded', String(expanded));
            height = details.getBoundingClientRect().height;
            target = height;
        };
        const animate = (open, from = details.getBoundingClientRect().height) => {
            expanded = open;
            motion?.cancel();
            clearTimeout(timeout);
            summary.setAttribute('aria-expanded', String(open));
            if (reduced.matches || !window.Rep0rterMotion) {
                settle();
                return;
            }
            // Keep the native content present until closing finishes.
            details.open = true;
            panel.inert = !open;
            details.dataset.filterMoving = '';
            height = from;
            target = naturalHeight();
            details.style.height = `${height}px`;
            timeout = setTimeout(settle, 1200);
            const collapsed = summary.getBoundingClientRect().height + 2;
            const full = panel.getBoundingClientRect().height;
            motion = window.Rep0rterMotion.spring({ from, to: target, velocity,
                onUpdate(value, speed) {
                    height = value;
                    velocity = speed;
                    details.style.height = `${Math.max(collapsed, value)}px`;
                    panel.style.opacity = String(Math.max(0, Math.min(1, (value - collapsed) / Math.max(full, 1))));
                }, onComplete: settle,
            });
        };
        const click = (event) => {
            if (event.defaultPrevented || event.button !== 0)
                return;
            event.preventDefault();
            animate(!expanded);
        };
        const focus = () => { if (motion && expanded)
            settle(); };
        const toggle = () => {
            // Ignore our temporary open attribute while animating a close.
            if (motion && details.open)
                return;
            if (details.open !== expanded) {
                expanded = details.open;
                settle();
            }
        };
        const preference = () => { if (reduced.matches)
            settle(); };
        summary.setAttribute('aria-expanded', String(expanded));
        summary.addEventListener('click', click);
        panel.addEventListener('focusin', focus);
        details.addEventListener('toggle', toggle);
        reduced.addEventListener('change', preference);
        const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
            if (!expanded || !details.open || !details.isConnected)
                return;
            const next = naturalHeight();
            if (Math.abs(next - target) > 1)
                animate(true, height);
        }) : null;
        observer?.observe(panel);
        disposeFilters = () => {
            observer?.disconnect();
            summary.removeEventListener('click', click);
            panel.removeEventListener('focusin', focus);
            details.removeEventListener('toggle', toggle);
            reduced.removeEventListener('change', preference);
            settle();
            summary.removeAttribute('aria-expanded');
        };
    };
    document.addEventListener('rep0rter:before-language', () => disposeFilters());
    document.addEventListener('rep0rter:language-applied', initializeFilters);
    window.addEventListener('pagehide', () => { cancelEntry(); disposeFilters(); });
    window.addEventListener('pageshow', event => { if (event.persisted)
        initializeFilters(); });
    if (document.readyState === 'loading')
        document.addEventListener('DOMContentLoaded', initializeFilters, { once: true });
    else
        initializeFilters();
})();
