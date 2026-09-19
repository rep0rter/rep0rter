// Decorative glyphs never replace or wrap the selectable, accessible source text.
(() => {
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  const contrast = matchMedia('(forced-colors: active)');
  const symbols = ['◇', '∴', '⋮', '⌁', '⌇', '┐', '└'];
  const duration = 760;
  const glyphLimit = 1600;
  const excluded = 'script,style,noscript,template,svg,math,canvas,textarea,input,select,option,[contenteditable]:not([contenteditable="false"]),[hidden],[inert],[aria-hidden="true"],.sr-only,.visually-hidden,.screen-reader-only,.enchantment-layer';
  let stopActive = () => {};
  let revision = 0;

  const allowed = () => !motion.matches && !contrast.matches && !document.hidden
    && typeof Intl.Segmenter === 'function' && typeof Highlight === 'function' && window.CSS?.highlights;
  const selected = () => Boolean(window.getSelection()?.toString());
  const visibleRect = rect => rect.width > 0 && rect.height > 0 && rect.bottom > 0
    && rect.top < innerHeight && rect.right > 0 && rect.left < innerWidth;
  function cancel() { revision += 1; stopActive(); }

  // Invert ease-in-out (.42, 0, .58, 1): find when the reveal reaches a radius.
  function arrival(fraction) {
    let lower = 0;
    let upper = 1;
    for (let step = 0; step < 18; step += 1) {
      const t = (lower + upper) / 2;
      if (3 * t * t - 2 * t * t * t < fraction) lower = t;
      else upper = t;
    }
    const t = (lower + upper) / 2;
    return 3 * (1 - t) ** 2 * t * .42 + 3 * (1 - t) * t * t * .58 + t ** 3;
  }

  function play(element = document.body, reveal = null) {
    cancel();
    if (!(element instanceof HTMLElement) || !allowed() || selected() || !element.isConnected) return () => {};
    const current = revision;
    // Grapheme boundaries are locale independent; an empty document lang is valid.
    const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
    const layer = document.createElement('div');
    layer.className = 'enchantment-layer';
    layer.setAttribute('aria-hidden', 'true');
    const cells = [];
    const animations = [];
    let frame = 0;
    let stopped = false;
    let observer;
    const stop = () => {
      if (stopped) return;
      stopped = true;
      cancelAnimationFrame(frame);
      observer?.disconnect();
      animations.forEach(animation => animation.cancel());
      CSS.highlights.delete('rep0rter-enchantment');
      layer.remove();
      if (stopActive === stop) stopActive = () => {};
    };
    stopActive = stop;
    try {
      const ranges = [];
      const header = document.querySelector('.site-header');
      const headerRect = header?.getBoundingClientRect();
      const styles = new WeakMap();
      const accepted = new WeakMap();
      const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
          const parent = node.parentElement;
          if (!node.data.trim() || !parent) return NodeFilter.FILTER_REJECT;
          if (!accepted.has(parent)) {
            let eligible = !parent.closest(excluded);
            const computed = getComputedStyle(parent);
            styles.set(parent, computed);
            if (computed.visibility !== 'visible' || computed.display === 'none') eligible = false;
            // Includes closed <details>, clipped labels, and transparent ancestors.
            if (typeof parent.checkVisibility === 'function'
              && !parent.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) eligible = false;
            if (!visibleRect(parent.getBoundingClientRect())) eligible = false;
            accepted.set(parent, eligible);
          }
          return accepted.get(parent) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        },
      });
      let node;
      while (cells.length < glyphLimit && (node = walker.nextNode())) {
        const computed = styles.get(node.parentElement);
        const nodeRange = document.createRange();
        nodeRange.selectNodeContents(node);
        if (![...nodeRange.getClientRects()].some(visibleRect)) continue;
        for (const part of segmenter.segment(node.data)) {
          if (cells.length >= glyphLimit) break;
          if (!part.segment.trim()) continue;
          const range = document.createRange();
          range.setStart(node, part.index);
          range.setEnd(node, part.index + part.segment.length);
          const rect = range.getBoundingClientRect();
          if (!visibleRect(rect)) continue;
          // Preserve native glass occlusion for even a partial glyph crossing
          // the sticky header; the decorative layer sits above that surface.
          if (headerRect && !header.contains(node.parentElement)
            && rect.top < headerRect.bottom && rect.bottom > headerRect.top
            && rect.left < headerRect.right && rect.right > headerRect.left) continue;
          const covering = document.elementFromPoint(
            Math.min(innerWidth - 1, Math.max(0, rect.left + rect.width / 2)),
            Math.min(innerHeight - 1, Math.max(0, rect.top + rect.height / 2))
          );
          const parent = node.parentElement;
          if (covering && !parent.contains(covering) && !covering.contains(parent)) continue;
          const cell = document.createElement('span');
          cell.className = 'enchantment-cell';
          Object.assign(cell.style, {
            left: `${rect.left}px`, top: `${rect.top}px`,
            fontFamily: computed.fontFamily, fontSize: computed.fontSize,
            fontWeight: computed.fontWeight, fontStyle: computed.fontStyle,
            fontStretch: computed.fontStretch, fontVariant: computed.fontVariant,
            fontFeatureSettings: computed.fontFeatureSettings,
            fontVariationSettings: computed.fontVariationSettings,
            letterSpacing: computed.letterSpacing, color: computed.color,
            textTransform: computed.textTransform, direction: computed.direction,
          });
          const text = document.createElement('span');
          text.className = 'enchantment-letter';
          text.textContent = part.segment;
          const rune = document.createElement('span');
          rune.className = 'enchantment-rune';
          rune.textContent = symbols[Math.floor(Math.random() * symbols.length)];
          cell.append(text, rune);
          layer.append(cell);
          const distance = reveal ? Math.hypot(
            Math.max(rect.left - reveal.x, 0, reveal.x - rect.right),
            Math.max(rect.top - reveal.y, 0, reveal.y - rect.bottom)
          ) : 0;
          const delay = reveal ? arrival(Math.min(1, distance / reveal.radius)) * reveal.duration + 16 : 0;
          cells.push({ cell, text, rune, rect, delay, next: delay + 70 + Math.random() * 40, node });
          ranges.push(range);
        }
      }
      if (!cells.length) { stop(); return stop; }
      // A transformed modal owns a separate top layer and coordinate space.
      // Keep it fully readable rather than painting glyphs behind the dialog.
      const modal = document.querySelector('dialog:modal');
      if (modal) {
        stop();
        return stop;
      }
      document.body.append(layer);
      const corrections = cells.map(({ text, rect }) => {
        const range = document.createRange();
        range.selectNodeContents(text);
        return rect.top - range.getBoundingClientRect().top;
      });
      cells.forEach((cell, index) => {
        cell.cell.style.top = `${cell.rect.top + corrections[index]}px`;
        cell.resolveAt = 380 + (index / Math.max(1, cells.length - 1)) * 280 + Math.random() * 12;
        const options = { duration, delay: cell.delay, fill: 'both', easing: 'linear' };
        animations.push(cell.text.animate([
          { opacity: 1, offset: 0 }, { opacity: .12, offset: .15 },
          { opacity: .12, offset: cell.resolveAt / duration },
          { opacity: 1, offset: (cell.resolveAt + 85) / duration }, { opacity: 1, offset: 1 },
        ], options));
        animations.push(cell.rune.animate([
          { opacity: 0, offset: 0 }, { opacity: .88, offset: .15 },
          { opacity: .88, offset: cell.resolveAt / duration },
          { opacity: 0, offset: (cell.resolveAt + 85) / duration }, { opacity: 0, offset: 1 },
        ], options));
      });
      // Hiding only exact text ranges leaves nested icons, controls, backgrounds
      // and the original DOM/layout intact. Copy and accessibility use the source.
      CSS.highlights.set('rep0rter-enchantment', new Highlight(...ranges));
      observer = new MutationObserver(records => {
        if (records.some(record => !layer.contains(record.target))) stop();
      });
      observer.observe(element, { childList: true, subtree: true, characterData: true });
      const started = performance.now();
      const end = duration + Math.max(...cells.map(cell => cell.delay));
      const tick = now => {
        if (current !== revision || !element.isConnected || !allowed() || now - started >= end) { stop(); return; }
        const elapsed = now - started;
        for (const cell of cells) {
          if (elapsed < cell.delay + cell.resolveAt && elapsed >= cell.next) {
            cell.rune.textContent = Math.random() < .18
              ? cell.text.textContent : symbols[Math.floor(Math.random() * symbols.length)];
            cell.next = elapsed + 70 + Math.random() * 40;
          }
        }
        frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
    } catch (_) {
      // Animation is optional. A failed measurement must never hide real text.
      stop();
    }
    return stop;
  }

  document.addEventListener('rep0rter:theme-reveal', event => {
    const { x, y, radius, duration: revealDuration } = event.detail || {};
    if (![x, y, radius, revealDuration].every(Number.isFinite) || radius <= 0) { cancel(); return; }
    play(document.body, { x, y, radius, duration: revealDuration });
  });
  document.addEventListener('rep0rter:theme-cancel', cancel);
  document.addEventListener('rep0rter:before-language', cancel);
  document.addEventListener('rep0rter:language-settled', event => {
    if (event.detail?.animate) play(document.body);
  });
  for (const event of ['pagehide', 'resize', 'scroll']) window.addEventListener(event, cancel, { passive: true });
  document.addEventListener('visibilitychange', () => { if (document.hidden) cancel(); });
  // A newly opened menu must cover ordinary text, never the decorative layer.
  // Pointer/keyboard activity restores native text before controls change state.
  document.addEventListener('pointerdown', cancel, { passive: true, capture: true });
  document.addEventListener('keydown', cancel, { capture: true });
  document.addEventListener('selectionchange', () => { if (selected()) cancel(); });
  motion.addEventListener('change', cancel);
  contrast.addEventListener('change', cancel);
  window.Rep0rterEnchantment = Object.freeze({ play, cancel, playAll: () => play(document.body) });
})();
