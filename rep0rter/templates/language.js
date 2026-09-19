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
    link.addEventListener('click', preservePosition);
  });
  preservePosition();
  // The URL is the reading choice. Returning to the root always opens English,
  // including browsers with a preference saved by an older version.
})();
