// Same-page image sheets: native modal semantics with interruptible spring motion.
(() => {
  let dispose = () => {};
  const initialize = () => {
    dispose();
    const removers = [];
    const listen = (target, type, handler) => {
      if (!target) return;
      target.addEventListener(type, handler);
      removers.push(() => target.removeEventListener(type, handler));
    };
    dispose = () => { removers.splice(0).forEach(remove => remove()); };
    const viewer = document.querySelector('#image-viewer');
    if (!viewer || typeof viewer.showModal !== 'function') return;
    const image = viewer.querySelector('[data-viewer-image]');
    const caption = viewer.querySelector('[data-viewer-caption]');
    const download = viewer.querySelector('[data-viewer-download]');
    const close = viewer.querySelector('[data-viewer-close]');
    const toolbar = viewer.querySelector('.image-viewer-toolbar');
    const root = document.documentElement;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    let opener = null;
    let animation = null;
    let progress = 0;
    let dragY = 0;
    let drag = null;
    let closing = false;
    let startedOutside = false;
    let origin = { x: 0, y: 20, scale: .96 };

    const render = () => {
      const remaining = 1 - progress;
      viewer.style.transform = `translate(${origin.x * remaining}px, ${origin.y * remaining + dragY}px) scale(${origin.scale + (1 - origin.scale) * progress})`;
      viewer.style.opacity = String(Math.min(1, Math.max(0, progress * 4)));
      root.style.setProperty('--viewer-depth', String(Math.max(0, Math.min(1, progress * (1 - Math.min(dragY / 500, .5))))));
    };
    const spring = (from, to, update, complete, velocity = 0) => {
      animation?.cancel();
      if (window.Rep0rterMotion && !reduced.matches) {
        animation = window.Rep0rterMotion.spring({ from, to, velocity, onUpdate: update, onComplete: complete });
      } else {
        update(to);
        complete?.();
      }
    };
    const cleanup = () => {
      animation?.cancel();
      animation = null;
      drag = null;
      closing = false;
      progress = 0;
      dragY = 0;
      root.classList.remove('image-viewer-open');
      root.style.removeProperty('--viewer-depth');
      viewer.style.removeProperty('transform');
      viewer.style.removeProperty('opacity');
      image.removeAttribute('src');
      image.removeAttribute('width');
      image.removeAttribute('height');
      image.alt = '';
      caption.textContent = '';
      download.removeAttribute('href');
      if (opener?.isConnected) opener.focus({ preventScroll: true });
      opener = null;
    };
    const removeListeners = dispose;
    dispose = () => {
      removeListeners();
      // A language replacement must not focus or scroll to the old document.
      opener = null;
      if (drag && toolbar.hasPointerCapture(drag.id)) toolbar.releasePointerCapture(drag.id);
      cleanup();
      if (viewer.open) viewer.close();
    };
    const finishClose = () => {
      viewer.close();
      // Native close events are queued: clean up now before another image opens.
      cleanup();
    };
    const dismiss = (velocity = 0) => {
      if (!viewer.open || closing) return;
      closing = true;
      drag = null;
      if (dragY > 0) {
        const from = dragY;
        spring(from, Math.max(innerHeight * .65, from + 180), value => {
          dragY = value;
          progress = Math.max(0, 1 - (value - from) / 240);
          render();
        }, finishClose, velocity);
      } else {
        spring(progress, 0, value => { progress = value; render(); }, finishClose);
      }
    };

    document.querySelectorAll('[data-image-view]').forEach(button => {
      button.disabled = false;
      listen(button, 'click', () => {
        if (viewer.open) return;
        const thumbnail = button.querySelector('img');
        const start = thumbnail.getBoundingClientRect();
        opener = button;
        startedOutside = false;
        closing = false;
        dragY = 0;
        image.src = button.dataset.imageSrc;
        image.alt = thumbnail.alt;
        image.width = thumbnail.naturalWidth || 1200;
        image.height = thumbnail.naturalHeight || 630;
        caption.textContent = thumbnail.alt;
        download.href = button.dataset.imageSrc;
        viewer.showModal();
        const end = viewer.getBoundingClientRect();
        origin = {
          x: start.left + start.width / 2 - end.left - end.width / 2,
          y: start.top + start.height / 2 - end.top - end.height / 2,
          scale: Math.max(.25, Math.min(.96, start.width / end.width)),
        };
        root.classList.add('image-viewer-open');
        progress = reduced.matches ? 1 : 0;
        render();
        spring(progress, 1, value => { progress = value; render(); });
      });
    });

    listen(close, 'click', () => dismiss());
    listen(viewer, 'cancel', event => {
      event.preventDefault();
      dismiss();
    });
    const isOutside = event => {
      const rect = viewer.getBoundingClientRect();
      return event.clientX < rect.left || event.clientX > rect.right ||
        event.clientY < rect.top || event.clientY > rect.bottom;
    };
    listen(viewer, 'pointerdown', event => {
      startedOutside = event.target === viewer && isOutside(event);
    });
    listen(viewer, 'click', event => {
      if (startedOutside && event.target === viewer && isOutside(event)) dismiss();
      startedOutside = false;
    });
    // A previous close event must never clear a newly opened image.
    listen(viewer, 'close', () => { if (!viewer.open) cleanup(); });

    // Drag the sheet's toolbar; image gestures and download controls stay native.
    listen(toolbar, 'pointerdown', event => {
      if (closing || event.button !== 0 || !event.isPrimary || event.target.closest('button, a')) return;
      animation?.cancel();
      progress = 1;
      drag = { id: event.pointerId, startY: event.clientY - dragY, lastY: event.clientY, time: event.timeStamp, velocity: 0 };
      toolbar.setPointerCapture(event.pointerId);
      render();
    });
    listen(toolbar, 'pointermove', event => {
      if (!drag || drag.id !== event.pointerId) return;
      const elapsed = event.timeStamp - drag.time;
      if (elapsed > 0) drag.velocity = (event.clientY - drag.lastY) / elapsed;
      drag.lastY = event.clientY;
      drag.time = event.timeStamp;
      dragY = Math.max(0, event.clientY - drag.startY);
      render();
    });
    const release = (event, cancelled = false) => {
      if (!drag || drag.id !== event.pointerId) return;
      const velocity = event.timeStamp - drag.time > 100 ? 0 : drag.velocity;
      drag = null;
      if (toolbar.hasPointerCapture(event.pointerId)) toolbar.releasePointerCapture(event.pointerId);
      if (!cancelled && (dragY > 110 || (dragY > 24 && velocity > .65))) {
        dismiss(velocity * 1000);
      } else {
        spring(dragY, 0, value => { dragY = value; render(); });
      }
    };
    listen(toolbar, 'pointerup', event => release(event));
    listen(toolbar, 'pointercancel', event => release(event, true));
    listen(toolbar, 'lostpointercapture', event => release(event, true));
  };
  document.addEventListener('rep0rter:before-language', () => dispose());
  document.addEventListener('rep0rter:language-applied', initialize);
  initialize();
})();
