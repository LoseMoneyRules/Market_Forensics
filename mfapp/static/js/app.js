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

  const realRole = document.querySelector('meta[name="mf-real-role"]')?.content || '';
  const effectiveRole = document.querySelector('meta[name="mf-effective-role"]')?.content || '';
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const workerChip = document.getElementById('mf-worker-chip');
  if (realRole !== 'CONTROL' || effectiveRole !== 'CONTROL' || !csrf) return;

  let busy = false;
  let stopped = false;
  let timer = null;

  const renderWorker = (state, suffix = '') => {
    if (!workerChip) return;
    const queued = Number(state?.queued || 0);
    const running = Number(state?.running || 0);
    const failed = Number(state?.failed || 0);
    if (running) workerChip.textContent = `Worker · running · ${queued} queued`;
    else if (queued) workerChip.textContent = `Worker · ${queued} queued${suffix}`;
    else if (failed) workerChip.textContent = `Worker · idle · ${failed} failed`;
    else workerChip.textContent = 'Worker · idle';
  };

  const updateJobRows = (processed) => {
    (processed || []).forEach((job) => {
      const row = document.querySelector(`[data-job-id="${job.job_id}"]`);
      if (!row) return;
      const status = row.querySelector('[data-job-status]');
      const attempts = row.querySelector('[data-job-attempts]');
      const error = row.querySelector('[data-job-error]');
      if (status) status.textContent = job.status || '';
      if (attempts) attempts.textContent = `${job.attempts ?? 0}/${job.max_attempts ?? 0}`;
      if (error) error.textContent = job.error || '—';
    });
  };

  const schedule = (ms) => {
    clearTimeout(timer);
    if (!stopped) timer = setTimeout(tick, ms);
  };

  const getStatus = async () => {
    const response = await fetch('/jobs/status', { credentials: 'same-origin', headers: { Accept: 'application/json' } });
    if (response.status === 401 || response.status === 403) { stopped = true; return null; }
    if (!response.ok) throw new Error(`status ${response.status}`);
    return response.json();
  };

  const pumpOne = async () => {
    const response = await fetch('/jobs/pump', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { Accept: 'application/json', 'X-CSRFToken': csrf },
    });
    if (response.status === 401 || response.status === 403) { stopped = true; return null; }
    if (!response.ok) throw new Error(`pump ${response.status}`);
    return response.json();
  };

  async function tick() {
    if (busy || stopped) return;
    busy = true;
    try {
      let state = await getStatus();
      if (!state) return;
      renderWorker(state);
      if (Number(state.due || 0) > 0) {
        renderWorker(state, ' · working');
        state = await pumpOne();
        if (!state) return;
        updateJobRows(state.processed);
        renderWorker(state);
        schedule(Number(state.due || 0) > 0 ? 700 : 5000);
      } else {
        schedule(Number(state.running || 0) > 0 ? 5000 : 8000);
      }
    } catch (error) {
      if (workerChip) workerChip.textContent = 'Worker · retrying';
      schedule(12000);
    } finally {
      busy = false;
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && !busy && !stopped) schedule(250);
  });
  schedule(300);
})();
