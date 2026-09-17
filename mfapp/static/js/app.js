(() => {
  document.querySelectorAll('.flash').forEach((el) => {
    setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateY(-4px)'; }, 4500);
    setTimeout(() => el.remove(), 5000);
  });
  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-copy]');
    if (!button) return;
    const input = document.querySelector(button.dataset.copy);
    if (!input || !navigator.clipboard) return;
    navigator.clipboard.writeText(input.value).then(() => {
      const old = button.textContent;
      button.textContent = 'Copied';
      setTimeout(() => { button.textContent = old; }, 1400);
    });
  });
})();
