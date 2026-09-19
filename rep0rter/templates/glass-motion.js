"use strict";
// One frame loop drives interruptible springs; content remains usable without it.
(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const finePointer = window.matchMedia('(hover: hover) and (pointer: fine)');
    const active = new Set();
    let frame = 0;
    let lastTime = 0;
    const finish = (motion) => {
        active.delete(motion);
        motion.onUpdate(motion.to, 0);
        motion.onComplete?.();
    };
    const tick = (now) => {
        frame = 0;
        // A background tab must never produce a huge integration step on return.
        const dt = Math.min(Math.max((now - lastTime) / 1000, 0), 0.032);
        lastTime = now;
        for (const motion of [...active]) {
            if (!active.has(motion))
                continue;
            // Analytic damped spring: mass 1, stiffness 600, damping 40.
            // This settles in roughly 400 ms with only a small natural overshoot.
            const displacement = motion.value - motion.to;
            const frequency = Math.sqrt(200);
            const decay = Math.exp(-20 * dt);
            const cosine = Math.cos(frequency * dt);
            const sine = Math.sin(frequency * dt);
            const slope = (motion.velocity + 20 * displacement) / frequency;
            motion.value = motion.to + decay * (displacement * cosine + slope * sine);
            motion.velocity = decay * (-20 * (displacement * cosine + slope * sine) +
                frequency * (-displacement * sine + slope * cosine));
            if (Math.abs(motion.value - motion.to) < motion.tolerance &&
                Math.abs(motion.velocity) < motion.tolerance * 20) {
                finish(motion);
            }
            else {
                motion.onUpdate(motion.value, motion.velocity);
            }
        }
        if (active.size && !frame)
            frame = requestAnimationFrame(tick);
    };
    const spring = ({ from, to, velocity = 0, onUpdate, onComplete }) => {
        const motion = {
            value: from, to, velocity, onUpdate, onComplete,
            tolerance: Math.max(Math.abs(to - from) * 0.001, 0.0001),
        };
        const handle = { cancel: () => {
                active.delete(motion);
                if (!active.size && frame) {
                    cancelAnimationFrame(frame);
                    frame = 0;
                }
            } };
        if (reduced.matches || (from === to && velocity === 0)) {
            finish(motion);
            return handle;
        }
        active.add(motion);
        if (!frame) {
            lastTime = performance.now();
            frame = requestAnimationFrame(tick);
        }
        return handle;
    };
    window.Rep0rterMotion = { spring };
    reduced.addEventListener('change', () => {
        if (!reduced.matches)
            return;
        cancelAnimationFrame(frame);
        frame = 0;
        for (const motion of [...active]) {
            if (active.has(motion))
                finish(motion);
        }
    });
    const initialize = () => {
        const root = document.documentElement;
        let scrollFrame = 0;
        const paintScroll = () => {
            scrollFrame = 0;
            root.style.setProperty('--nav-depth', Math.min(Math.max(window.scrollY / 120, 0), 1).toFixed(3));
        };
        window.addEventListener('scroll', () => {
            if (!scrollFrame)
                scrollFrame = requestAnimationFrame(paintScroll);
        }, { passive: true });
        paintScroll();
        // Delegation avoids a listener per card. Only one surface updates per frame.
        const surfaces = '.masthead, .story-card, .about-card, .subscribe-card, .button, .image-viewer-panel';
        let highlight = null;
        let pendingPointer = null;
        let pointerFrame = 0;
        const clearHighlight = () => {
            highlight?.style.removeProperty('--glass-light');
            highlight = null;
        };
        const paintPointer = () => {
            pointerFrame = 0;
            if (!pendingPointer || reduced.matches || !finePointer.matches) {
                clearHighlight();
                return;
            }
            const { target, x, y } = pendingPointer;
            if (target !== highlight)
                clearHighlight();
            if (!target?.isConnected)
                return;
            highlight = target;
            const bounds = target.getBoundingClientRect();
            const percentage = (point, origin, size) => `${Math.min(100, Math.max(0, (point - origin) / Math.max(size, 1) * 100)).toFixed(2)}%`;
            target.style.setProperty('--glass-x', percentage(x, bounds.left, bounds.width));
            target.style.setProperty('--glass-y', percentage(y, bounds.top, bounds.height));
            target.style.setProperty('--glass-light', '1');
        };
        document.addEventListener('pointermove', event => {
            if (!finePointer.matches || reduced.matches || event.pointerType === 'touch')
                return;
            pendingPointer = {
                target: event.target instanceof Element ? event.target.closest(surfaces) : null,
                x: event.clientX, y: event.clientY,
            };
            if (!pointerFrame)
                pointerFrame = requestAnimationFrame(paintPointer);
        }, { passive: true });
        const resetPointer = () => {
            pendingPointer = null;
            clearHighlight();
        };
        document.addEventListener('pointerleave', resetPointer);
        window.addEventListener('blur', resetPointer);
        reduced.addEventListener('change', resetPointer);
        finePointer.addEventListener('change', resetPointer);
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initialize, { once: true });
    }
    else {
        initialize();
    }
})();
