// Real links work without JavaScript; enhancement preserves reading position.
(() => {
  const links = [...document.querySelectorAll('[data-language]')];
  let visibleArticle = null;
  const observer = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (entry.isIntersecting) visibleArticle = entry.target.id;
    }
  }, { rootMargin: '-10% 0px -60% 0px' }) : null;
  document.querySelectorAll('article[id]').forEach(article => observer?.observe(article));
  const preservePosition = () => {
    const anchor = visibleArticle ? '#' + encodeURIComponent(visibleArticle) : location.hash;
    links.forEach(link => { link.hash = anchor; });
  };
  links.forEach(link => {
    link.addEventListener('pointerdown', preservePosition);
    link.addEventListener('click', () => {
      preservePosition();
      try { localStorage.setItem('rep0rter-language', link.dataset.language); } catch (_) { /* optional storage */ }
    });
  });
  preservePosition();
  // Explicit locale URLs always win over remembered choices.
  if (location.pathname.endsWith('/') && !location.hash) {
    try {
      const saved = localStorage.getItem('rep0rter-language');
      const target = links.find(link => link.dataset.language === saved);
      if (target && saved !== document.documentElement.lang) location.replace(target.href);
    } catch (_) { /* language links remain usable */ }
  }
})();
