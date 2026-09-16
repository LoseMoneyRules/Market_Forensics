(() => {
  const dataNode = document.getElementById('flowData');
  const yearSelect = document.getElementById('flowYear');
  if (!dataNode || !yearSelect) return;

  let all = {};
  try { all = JSON.parse(dataNode.textContent || '{}'); } catch (_) { all = {}; }
  const years = Object.keys(all).sort((a,b) => Number(b) - Number(a));

  const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const compact = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    const a = Math.abs(n);
    if (a >= 1e12) return `$${(n/1e12).toFixed(2)}T`;
    if (a >= 1e9) return `$${(n/1e9).toFixed(2)}B`;
    if (a >= 1e6) return `$${(n/1e6).toFixed(1)}M`;
    if (a >= 1e3) return `$${(n/1e3).toFixed(1)}K`;
    return `$${n.toLocaleString(undefined,{maximumFractionDigits:0})}`;
  };
  const percent = (value) => {
    const n = Number(value);
    return Number.isFinite(n) ? `${(n*100).toFixed(1)}%` : '—';
  };

  const table = (flow) => {
    if (!flow || !flow.ok) return `<div class="empty-state compact"><p>${esc(flow?.reason || 'No flow available.')}</p></div>`;
    const rows = flow.rows || [];
    const pctKey = rows.some(r => Object.prototype.hasOwnProperty.call(r, '% Revenue')) ? '% Revenue' : '% CFO';
    return `<table class="data-table"><thead><tr><th>Line</th><th>Amount</th><th>${esc(pctKey)}</th></tr></thead><tbody>${rows.map(r => `<tr><td>${esc(r.Line)}</td><td>${compact(r.Amount)}</td><td>${percent(r[pctKey])}</td></tr>`).join('')}</tbody></table>`;
  };

  const draw = (elementId, flow) => {
    const el = document.getElementById(elementId);
    if (!el) return;
    if (!flow || !flow.ok) {
      el.innerHTML = `<div class="empty-state compact"><p>${esc(flow?.reason || 'No flow available.')}</p></div>`;
      return;
    }
    if (!window.Plotly) {
      el.innerHTML = '<div class="empty-state compact"><p>Chart library unavailable. The audited table remains available below.</p></div>';
      return;
    }
    const labels = flow.labels || [];
    const links = flow.links || [];
    const source = links.map(x => Number(x[0]));
    const target = links.map(x => Number(x[1]));
    const value = links.map(x => Number(x[2]));
    const nodeColors = labels.map((_, i) => ['#0b1724','#176b67','#b28643','#687883','#d9e1e5'][i % 5]);
    Plotly.react(el, [{
      type: 'sankey',
      arrangement: 'snap',
      orientation: 'h',
      valueformat: ',.0f',
      node: {label: labels, color: nodeColors, pad: 22, thickness: 18, line: {color:'#cfd8dc', width:0.7}},
      link: {source, target, value, color: 'rgba(23,107,103,0.20)'}
    }], {
      margin: {l:12,r:12,t:12,b:12},
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      font: {family:'Arial, Helvetica, sans-serif', size:12, color:'#172431'},
      height: 430
    }, {displayModeBar:false, responsive:true});
  };

  const render = (year) => {
    const pack = all[String(year)] || {};
    const income = pack.income || {};
    const cash = pack.cash || {};
    draw('incomeSankey', income);
    draw('cashSankey', cash);
    const incomeBasis = document.getElementById('incomeBasis');
    const cashBasis = document.getElementById('cashBasis');
    const incomeTable = document.getElementById('incomeTable');
    const cashTable = document.getElementById('cashTable');
    if (incomeBasis) incomeBasis.textContent = income.basis || income.reason || '';
    if (cashBasis) cashBasis.textContent = cash.basis || cash.reason || '';
    if (incomeTable) incomeTable.innerHTML = table(income);
    if (cashTable) cashTable.innerHTML = table(cash);
  };

  if (!years.length) {
    yearSelect.innerHTML = '<option>No FY data</option>';
    render('');
    return;
  }
  years.forEach(y => {
    const option = document.createElement('option');
    option.value = y; option.textContent = `FY ${y}`;
    yearSelect.appendChild(option);
  });
  yearSelect.value = years[0];
  yearSelect.addEventListener('change', () => render(yearSelect.value));
  render(yearSelect.value);
})();
