"use strict";
// Same-page landscape cards. The artwork stays intact; only its frame catches light.
(() => {
    let dispose = () => { };
    const initialize = () => {
        dispose();
        const removers = [];
        const listen = (target, type, handler) => {
            // Test harnesses stub matchMedia with plain objects that cannot listen.
            if (typeof target?.addEventListener !== 'function')
                return;
            target.addEventListener(type, handler);
            removers.push(() => target.removeEventListener(type, handler));
        };
        dispose = () => { removers.splice(0).forEach(remove => remove()); };
        const viewer = document.querySelector('#image-viewer');
        if (!viewer || typeof viewer.showModal !== 'function')
            return;
        // The dialog template always ships the image, caption and download link;
        // the close button, stage, card and status line are optional.
        const image = viewer.querySelector('[data-viewer-image]');
        const caption = viewer.querySelector('[data-viewer-caption]');
        const download = viewer.querySelector('[data-viewer-download]');
        const close = viewer.querySelector('[data-viewer-close]');
        const stage = viewer.querySelector('[data-viewer-stage]');
        const card = viewer.querySelector('[data-viewer-card]');
        const status = viewer.querySelector('[data-viewer-status]');
        const root = document.documentElement;
        const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
        let opener = null;
        let animation = null;
        let progress = 0;
        let closing = false;
        let startedOutside = false;
        let touch = null;
        const axes = {
            x: { value: 0, velocity: 0 }, y: { value: 0, velocity: 0 }, light: { value: 0, velocity: 0 },
        };
        const paintCard = () => {
            if (!card)
                return;
            card.style.setProperty('--card-rx', `${-axes.y.value * 4}deg`);
            card.style.setProperty('--card-ry', `${axes.x.value * 5}deg`);
            card.style.setProperty('--card-x', `${50 + axes.x.value * 40}%`);
            card.style.setProperty('--card-y', `${50 + axes.y.value * 40}%`);
            card.style.setProperty('--card-light', String(axes.light.value));
        };
        const tilt = (x = 0, y = 0, light = 0, immediate = false) => {
            for (const [key, to] of Object.entries({ x, y, light })) {
                const axis = axes[key];
                axis.motion?.cancel();
                axis.motion = null;
                if (!immediate && !reduced.matches && window.Rep0rterMotion) {
                    axis.motion = window.Rep0rterMotion.spring({
                        from: axis.value, to, velocity: axis.velocity,
                        onUpdate: (value, velocity) => { axis.value = value; axis.velocity = velocity; paintCard(); },
                    });
                }
                else {
                    axis.value = to;
                    axis.velocity = 0;
                }
            }
            paintCard();
        };
        const render = () => {
            // Keep the page still and controls level; no long flight from the thumbnail.
            viewer.style.transform = `translateY(${(1 - progress) * 12}px) scale(${.98 + .02 * progress})`;
            viewer.style.opacity = String(Math.min(1, Math.max(0, progress)));
            root.style.setProperty('--viewer-depth', String(Math.max(0, Math.min(1, progress))));
        };
        const spring = (to, complete) => {
            animation?.cancel();
            if (window.Rep0rterMotion && !reduced.matches) {
                animation = window.Rep0rterMotion.spring({ from: progress, to,
                    onUpdate: value => { progress = value; render(); }, onComplete: complete });
            }
            else {
                progress = to;
                render();
                complete?.();
            }
        };
        const imageState = (state) => {
            viewer.dataset.imageState = state;
            if (status)
                status.textContent = state === 'ready' ? '' : status.dataset[state === 'error' ? 'error' : 'loading'] ?? '';
            download.hidden = state !== 'ready';
        };
        const syncImages = () => {
            const dark = root.dataset.theme === 'dark' || (!root.dataset.theme && window.matchMedia('(prefers-color-scheme: dark)').matches);
            const pending = [];
            document.querySelectorAll('[data-image-light][data-image-dark]').forEach(element => {
                // The selector requires both attributes, so the chosen one is present.
                const src = (dark ? element.dataset.imageDark : element.dataset.imageLight);
                if (element.matches('a')) {
                    element.href = src;
                    return;
                }
                element.dataset.imageSrc = src;
                const source = element.querySelector('[data-theme-source]');
                const media = dark ? 'all' : 'not all';
                if (source && source.media !== media)
                    source.media = media;
                const thumbnail = element.querySelector('img');
                const rect = thumbnail.getBoundingClientRect();
                // Only wait for visible images, never force the lazy-loaded article list.
                if (thumbnail.decode && rect.bottom > 0 && rect.top < window.innerHeight && rect.width > 0) {
                    pending.push(thumbnail.decode().catch(() => { }));
                }
            });
            if (viewer.open && opener && image.getAttribute('src') !== opener.dataset.imageSrc) {
                imageState('loading');
                image.src = opener.dataset.imageSrc;
                download.href = opener.dataset.imageSrc;
                if (image.complete)
                    imageState(image.naturalWidth ? 'ready' : 'error');
                if (image.decode)
                    pending.push(image.decode().catch(() => { }));
            }
            // Give a theme snapshot decoded artwork, with a bound for slow networks.
            if (!pending.length)
                return;
            return new Promise(resolve => {
                const timeout = window.setTimeout(resolve, 400);
                Promise.all(pending).then(() => { window.clearTimeout(timeout); resolve(); });
            });
        };
        const imageAPI = { sync: syncImages };
        window.Rep0rterImages = imageAPI;
        const observer = typeof MutationObserver === 'function' ? new MutationObserver(syncImages) : null;
        observer?.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
        const cleanup = () => {
            animation?.cancel();
            animation = null;
            touch = null;
            tilt(0, 0, 0, true);
            closing = false;
            progress = 0;
            root.classList.remove('image-viewer-open');
            root.style.removeProperty('--viewer-depth');
            viewer.style.removeProperty('transform');
            viewer.style.removeProperty('opacity');
            delete viewer.dataset.imageState;
            image.removeAttribute('src');
            image.alt = '';
            caption.textContent = '';
            if (status)
                status.textContent = '';
            download.removeAttribute('href');
            download.hidden = true;
            if (opener?.isConnected)
                opener.focus({ preventScroll: true });
            opener = null;
        };
        const removeListeners = dispose;
        dispose = () => {
            removeListeners();
            observer?.disconnect();
            if (window.Rep0rterImages === imageAPI)
                delete window.Rep0rterImages;
            // Locale replacement must not return focus to a detached document.
            opener = null;
            cleanup();
            if (viewer.open)
                viewer.close();
        };
        const dismiss = () => {
            if (!viewer.open || closing)
                return;
            closing = true;
            touch = null;
            tilt(0, 0, 0, true);
            spring(0, () => { viewer.close(); cleanup(); });
        };
        document.querySelectorAll('[data-image-view]').forEach(button => {
            button.disabled = false;
            listen(button, 'click', () => {
                if (viewer.open)
                    return;
                const thumbnail = button.querySelector('img');
                opener = button;
                startedOutside = false;
                closing = false;
                imageState('loading');
                image.alt = thumbnail.alt;
                caption.textContent = thumbnail.alt;
                download.href = button.dataset.imageSrc;
                image.src = button.dataset.imageSrc;
                root.classList.add('image-viewer-open');
                viewer.showModal();
                close?.focus({ preventScroll: true });
                if (image.complete)
                    imageState(image.naturalWidth ? 'ready' : 'error');
                progress = reduced.matches ? 1 : 0;
                render();
                spring(1);
            });
        });
        listen(image, 'load', () => { if (viewer.open && image.naturalWidth)
            imageState('ready'); });
        listen(image, 'error', () => { if (viewer.open && image.getAttribute('src'))
            imageState('error'); });
        listen(close, 'click', dismiss);
        listen(viewer, 'cancel', event => { event.preventDefault(); dismiss(); });
        const isOutside = (event) => {
            const rect = viewer.getBoundingClientRect();
            return event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
        };
        listen(viewer, 'pointerdown', event => { startedOutside = event.target === viewer && isOutside(event); });
        listen(viewer, 'click', event => {
            if (startedOutside && event.target === viewer && isOutside(event))
                dismiss();
            startedOutside = false;
        });
        // A queued close event must never erase a newly opened image.
        listen(viewer, 'close', () => { if (!viewer.open)
            cleanup(); });
        const point = (event) => {
            if (!card || !stage || reduced.matches || closing || viewer.dataset.imageState !== 'ready')
                return;
            const rect = stage.getBoundingClientRect(); // Stationary bounds prevent tilt feedback.
            const clamp = (value) => Math.max(-1, Math.min(1, value));
            tilt(clamp((event.clientX - rect.left) / rect.width * 2 - 1), clamp((event.clientY - rect.top) / rect.height * 2 - 1), 1);
        };
        listen(stage, 'pointerdown', event => {
            if (!event.isPrimary) {
                touch = null;
                tilt();
                return;
            }
            if (event.pointerType !== 'mouse') {
                touch = event.pointerId;
                point(event);
            }
        });
        listen(stage, 'pointermove', event => {
            if (event.pointerType === 'mouse' || event.pointerId === touch)
                point(event);
        });
        const resetTilt = () => { touch = null; tilt(); };
        listen(stage, 'pointerleave', resetTilt);
        listen(stage, 'pointerup', event => { if (event.pointerType !== 'mouse')
            resetTilt(); });
        listen(stage, 'pointercancel', resetTilt);
        listen(window, 'blur', resetTilt);
        listen(reduced, 'change', () => { if (reduced.matches)
            tilt(0, 0, 0, true); });
        syncImages();
    };
    document.addEventListener('rep0rter:before-language', () => dispose());
    document.addEventListener('rep0rter:language-applied', initialize);
    window.addEventListener?.('pagehide', () => dispose());
    window.addEventListener?.('pageshow', event => { if (event.persisted)
        initialize(); });
    initialize();
})();
