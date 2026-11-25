for (const button of document.querySelectorAll('.copy-button')) {
  const group = button.closest('.code-group');
  const code = group.querySelector('code');
  const status = group.querySelector('.copy-status');

  button.addEventListener('click', async () => {
    status.textContent = '';
    try {
      await navigator.clipboard.writeText(code.textContent);
      status.textContent = 'Copied to clipboard.';
    } catch {
      const range = document.createRange();
      range.selectNodeContents(code);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      status.textContent = 'Commands selected. Use your browser’s copy command.';
    }
  });
  button.hidden = false;
}

if ('IntersectionObserver' in window && matchMedia('(prefers-reduced-motion: no-preference)').matches) {
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.classList.add('reveal-in');
        observer.unobserve(entry.target);
      }
    }
  }, { threshold: 0.12 });

  for (const element of document.querySelectorAll('[data-reveal]')) {
    observer.observe(element);
  }
}
