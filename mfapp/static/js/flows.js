(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const fmt = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return '—';
    const abs = Math.abs(n);
    if (abs >= 1e12) return `${(n/1e12).toFixed(2)}T`;
    if (abs >= 1e9) return `${(n/1e9).toFixed(2)}B`;
    if (abs >= 1e6) return `${(n/1e6).toFixed(2)}M`;
    return n.toLocaleString(undefined,{maximumFractionDigits:0});
  };

  document.querySelectorAll('.flow-tab').forEach((button) => {
    button.addEventListener('click', () => {
      document.querySelectorAll('.flow-tab').forEach((x) => x.classList.remove('active'));
      document.querySelectorAll('.flow-panel').forEach((x) => x.classList.remove('active'));
      button.classList.add('active');
      document.getElementById(button.dataset.flowTarget)?.classList.add('active');
    });
  });

  function render(root) {
    let data = {};
    try { data = JSON.parse(root.dataset.flow || '{}'); } catch (_) {}
    const edges = Array.isArray(data.edges) ? data.edges.filter((e) => Number(e.value) >= 0) : [];
    const exceptions = Array.isArray(data.signed_exceptions) ? data.signed_exceptions : [];

    const title = document.createElement('div');
    title.className = 'flow-title';
    title.innerHTML = `<div><strong>${data.flow_type || 'Financial Flow'}</strong><div class="muted">${data.period || ''}</div></div><span class="status-chip">${data.calculation_version || ''}</span>`;
    root.appendChild(title);

    if (!edges.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.innerHTML = '<h2>No positive flow edges stored</h2><p>Missing or negative facts are never converted into fake positive widths.</p>';
      root.appendChild(empty);
    } else {
      const nodes = [...new Set(edges.flatMap((e) => [e.source, e.target]))];
      const incoming = new Map(nodes.map((n) => [n, []]));
      const outgoing = new Map(nodes.map((n) => [n, []]));
      edges.forEach((e) => { outgoing.get(e.source).push(e); incoming.get(e.target).push(e); });
      const depth = new Map();
      const roots = nodes.filter((n) => incoming.get(n).length === 0);
      const queue = roots.map((n) => [n,0]);
      while (queue.length) {
        const [n,d] = queue.shift();
        if ((depth.get(n) ?? -1) >= d) continue;
        depth.set(n,d);
        outgoing.get(n).forEach((e) => queue.push([e.target,d+1]));
      }
      nodes.forEach((n) => { if (!depth.has(n)) depth.set(n,0); });
      const maxDepth = Math.max(...depth.values(), 1);
      const columns = Array.from({length:maxDepth+1}, () => []);
      nodes.forEach((n) => columns[depth.get(n)].push(n));
      const width = Math.max(760, root.clientWidth - 36);
      const maxRows = Math.max(...columns.map((c) => c.length), 1);
      const height = Math.max(340, maxRows * 92 + 60);
      const svg = document.createElementNS(NS,'svg');
      svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
      svg.setAttribute('width','100%');
      svg.setAttribute('height',String(height));
      svg.setAttribute('role','img');
      svg.setAttribute('aria-label',`${data.flow_type || 'Financial'} flow ${data.period || ''}`);
      const pos = new Map();
      const nodeW = 145, nodeH = 56, side = 20;
      columns.forEach((col, ci) => {
        const x = side + (width - side*2 - nodeW) * (ci / maxDepth);
        const gap = height / (col.length + 1);
        col.forEach((n, ri) => pos.set(n,{x,y:gap*(ri+1)-nodeH/2}));
      });
      const maxValue = Math.max(1, ...edges.map((e) => Number(e.value)||0));
      edges.forEach((e) => {
        const a=pos.get(e.source), b=pos.get(e.target); if(!a||!b) return;
        const x1=a.x+nodeW, y1=a.y+nodeH/2, x2=b.x, y2=b.y+nodeH/2;
        const c1=x1+(x2-x1)*.45, c2=x1+(x2-x1)*.55;
        const path=document.createElementNS(NS,'path');
        path.setAttribute('d',`M ${x1} ${y1} C ${c1} ${y1}, ${c2} ${y2}, ${x2} ${y2}`);
        path.setAttribute('fill','none'); path.setAttribute('stroke','#6ba9aa'); path.setAttribute('stroke-opacity','.44');
        path.setAttribute('stroke-width',String(Math.max(2,Math.min(34,(Number(e.value)||0)/maxValue*34))));
        path.setAttribute('stroke-linecap','round');
        const t=document.createElementNS(NS,'title'); t.textContent=`${e.source} → ${e.target}: ${fmt(e.value)}`; path.appendChild(t); svg.appendChild(path);
      });
      nodes.forEach((n) => {
        const p=pos.get(n); const group=document.createElementNS(NS,'g');
        const rect=document.createElementNS(NS,'rect'); rect.setAttribute('x',p.x);rect.setAttribute('y',p.y);rect.setAttribute('width',nodeW);rect.setAttribute('height',nodeH);rect.setAttribute('rx','7');rect.setAttribute('fill','#fbfcfd');rect.setAttribute('stroke','#bfcbd4');group.appendChild(rect);
        const label=document.createElementNS(NS,'text');label.setAttribute('x',p.x+10);label.setAttribute('y',p.y+23);label.setAttribute('font-size','11');label.setAttribute('font-weight','700');label.setAttribute('fill','#0b1f33');label.textContent=n.length>21?`${n.slice(0,20)}…`:n;group.appendChild(label);
        const vals=[...incoming.get(n),...outgoing.get(n)].map((e)=>Number(e.value)||0); const value=Math.max(...vals,0);
        const val=document.createElementNS(NS,'text');val.setAttribute('x',p.x+10);val.setAttribute('y',p.y+42);val.setAttribute('font-size','12');val.setAttribute('fill','#607384');val.textContent=fmt(value);group.appendChild(val); svg.appendChild(group);
      });
      root.appendChild(svg);
    }

    if (exceptions.length) {
      const box = document.createElement('div');
      box.className = 'flow-exceptions';
      box.innerHTML = '<strong>Signed exceptions — excluded from positive widths</strong>';
      exceptions.forEach((edge) => {
        const row = document.createElement('div');
        row.className = 'flow-exception';
        row.innerHTML = `<span>${edge.source} → ${edge.target}</span><strong>${fmt(edge.value)}</strong>`;
        box.appendChild(row);
      });
      root.appendChild(box);
    }
    if (Array.isArray(data.derived) && data.derived.length) {
      const note = document.createElement('p'); note.className='muted'; note.textContent=`Derived bridge: ${data.derived.join('; ')}`; root.appendChild(note);
    }
  }
  document.querySelectorAll('.flow-canvas').forEach(render);
})();
