// Apply the saved appearance before CSS paints. Storage is optional.
(() => {
  const root = document.documentElement;
  const key = 'rep0rter-theme';
  const modes = ['light', 'dark', 'system'];
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  let preference = 'system';
  try {
    const saved = localStorage.getItem(key);
    if (modes.includes(saved)) preference = saved;
  } catch (_) { /* Reading still works when browser storage is blocked. */ }

  const apply = () => {
    root.dataset.theme = preference === 'system' ? (system.matches ? 'dark' : 'light') : preference;
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === preference));
    });
    document.querySelectorAll('[data-theme-trigger]').forEach(button => {
      const label = button.dataset['theme' + preference[0].toUpperCase() + preference.slice(1)];
      const description = `${button.dataset.themeLabel}: ${label}`;
      button.dataset.themePreference = preference;
      button.setAttribute('aria-label', description);
      button.setAttribute('title', description);
      button.querySelectorAll('[data-theme-icon]').forEach(icon => {
        icon.hidden = icon.dataset.themeIcon !== preference;
      });
    });
  };
  apply();
  system.addEventListener('change', apply);
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    preference = modes.includes(event.newValue) ? event.newValue : 'system';
    apply();
  });
  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('[data-theme-controls]').forEach(group => { group.hidden = false; });
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.addEventListener('click', () => {
        preference = button.dataset.themeChoice;
        try { localStorage.setItem(key, preference); } catch (_) { /* Session-only choice. */ }
        apply();
        const menu = button.closest('.preference-menu');
        if (menu) {
          menu.open = false;
          menu.querySelector('summary').focus({ preventScroll: true });
        }
      });
    });
    const menus = [...document.querySelectorAll('.preference-menu')];
    menus.forEach(menu => {
      menu.querySelector('summary').addEventListener('click', () => {
        menus.forEach(other => { if (other !== menu) other.open = false; });
      });
    });
    document.addEventListener('pointerdown', event => {
      menus.forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
    });
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      const open = menus.find(menu => menu.open);
      if (!open) return;
      event.preventDefault();
      open.open = false;
      open.querySelector('summary').focus({ preventScroll: true });
    });
    apply();
  });
})();
