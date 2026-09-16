document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-copy]');
  if (!button) return;
  const input = document.querySelector(button.dataset.copy);
  if (!input) return;
  navigator.clipboard.writeText(input.value).then(() => {
    const old = button.textContent;
    button.textContent = 'Copied';
    setTimeout(() => button.textContent = old, 1400);
  });
});
