// Language choices are ordinary links. Never move the reader with script.
(() => {
  const links = [...document.querySelectorAll('[data-language]')];
  let destination = null;
  const updateLinks = () => links.forEach(link => {
    link.search = location.search || '';
    // A previous report/section anchor must not jump the new edition downward.
    link.hash = '';
  });
  updateLinks();
  links.forEach(link => {
    link.addEventListener('pointerdown', updateLinks);
    link.addEventListener('click', event => {
      updateLinks();
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey ||
          event.shiftKey || event.altKey || (link.target && link.target !== '_self') || link.hasAttribute('download')) return;
      if (link.getAttribute('aria-current') === 'page') {
        event.preventDefault();
        const menu = link.closest('.preference-menu');
        if (menu) {
          menu.open = false;
          menu.querySelector('summary').focus({ preventScroll: true });
        }
        return;
      }
      destination = link.href;
    });
  });
  window.addEventListener('pageswap', event => {
    const target = event.activation?.entry?.url;
    // Translation changes must not make report cards fly between page layouts.
    if (destination && (!target || target === destination)) event.viewTransition?.skipTransition();
    destination = null;
  });
})();
