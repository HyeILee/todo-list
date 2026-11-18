(() => {
  const compose = document.querySelector('[data-memo-compose]');
  if (!compose || typeof HTMLDialogElement === 'undefined' || !HTMLDialogElement.prototype.showModal) return;
  const fallback = compose.closest('details');
  const dialog = document.createElement('dialog');
  dialog.className = 'memo-dialog';
  dialog.setAttribute('aria-labelledby', 'new-memo-title');
  dialog.append(compose);
  document.body.append(dialog);
  fallback.remove();
  const close = compose.querySelector('[data-memo-close]');
  close.hidden = false;
  close.addEventListener('click', () => dialog.close());
  document.querySelectorAll('[data-memo-open]').forEach(link => {
    link.setAttribute('aria-haspopup', 'dialog');
    link.addEventListener('click', event => {
      event.preventDefault();
      dialog.showModal();
      compose.querySelector('textarea').focus();
    });
  });
  if (compose.hasAttribute('data-has-errors')) dialog.showModal();
  const menus = document.querySelectorAll('.memo-menu');
  menus.forEach(menu => menu.addEventListener('toggle', () => {
    if (menu.open) menus.forEach(other => { if (other !== menu) other.open = false; });
  }));
  document.addEventListener('click', event => {
    menus.forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') menus.forEach(menu => { menu.open = false; });
  });
})();
