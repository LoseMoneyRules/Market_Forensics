(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const body = document.body;
  const menuButton = document.getElementById('mf-mobile-menu');
  const toolsButton = document.getElementById('mf-mobile-tools');
  const backdrop = document.getElementById('mf-mobile-backdrop');
  const closeMobile = () => { body.classList.remove('nav-open'); body.classList.remove('tools-open'); };
  menuButton?.addEventListener('click', () => { body.classList.toggle('nav-open'); body.classList.remove('tools-open'); });
  toolsButton?.addEventListener('click', () => { body.classList.toggle('tools-open'); body.classList.remove('nav-open'); });
  backdrop?.addEventListener('click', closeMobile);

  document.querySelectorAll('.company-tabs').forEach((tabs) => {
    const active = tabs.querySelector('a.active')?.textContent?.trim() || 'Research steps';
    const toggle = document.createElement('button');
    toggle.type = 'button'; toggle.className = 'company-tabs-toggle';
    toggle.innerHTML = `<span>Research step</span><strong>${active}</strong>`;
    tabs.parentNode?.insertBefore(toggle, tabs);
    toggle.addEventListener('click', () => tabs.classList.toggle('mobile-open'));
  });

  const ticker = document.querySelector('.ticker-badge')?.textContent?.trim();
  const priceNode = document.querySelector('[data-live-price]');
  const priceMeta = document.querySelector('[data-live-price-meta]');
  const marketBox = priceNode?.closest('.company-market');
  const fmtTime = (iso) => {
    if (!iso) return '';
    const d = new Date(iso); if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
  };
  const readQuote = async () => {
    if (!ticker) return;
    try {
      const r = await fetch(`/company/${encodeURIComponent(ticker)}/price/live`, {credentials:'same-origin', headers:{Accept:'application/json'}});
      if (!r.ok) return;
      const q = await r.json();
      if (priceNode && Number.isFinite(Number(q.price))) priceNode.textContent = `$${Number(q.price).toFixed(2)}`;
      if (priceMeta) priceMeta.innerHTML = `<i class="live-dot"></i>${q.provider || 'quote'} · ${fmtTime(q.as_of)}`;
    } catch (_) {}
  };
  const requestQuote = async () => {
    if (!ticker || !csrf) return;
    marketBox?.classList.add('refreshing');
    try {
      await fetch(`/company/${encodeURIComponent(ticker)}/price/refresh`, {method:'POST', credentials:'same-origin', headers:{Accept:'application/json','X-CSRFToken':csrf}});
      setTimeout(readQuote, 2500); setTimeout(readQuote, 9000);
    } catch (_) {} finally { setTimeout(() => marketBox?.classList.remove('refreshing'), 3000); }
  };
  if (ticker && location.pathname.includes('/company/')) {
    setTimeout(requestQuote, 1200);
    setInterval(requestQuote, 5 * 60 * 1000);
  }

  const parse = (node, key='series') => { try { return JSON.parse(node.dataset[key] || '[]'); } catch (_) { return []; } };
  const css = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const compact = (v) => {
    const n = Number(v); if (!Number.isFinite(n)) return '—'; const a=Math.abs(n);
    if(a>=1e12)return `${(n/1e12).toFixed(1)}T`; if(a>=1e9)return `${(n/1e9).toFixed(1)}B`; if(a>=1e6)return `${(n/1e6).toFixed(1)}M`; if(a>=1e3)return `${(n/1e3).toFixed(1)}K`; return n.toFixed(1);
  };
  function lineChart(canvas, rows, defs, percent=false) {
    if (!canvas || !rows.length) return;
    const rect=canvas.getBoundingClientRect(), dpr=Math.min(window.devicePixelRatio||1,2), w=Math.max(300,rect.width), h=Math.max(180,rect.height);
    canvas.width=w*dpr; canvas.height=h*dpr; const ctx=canvas.getContext('2d'); ctx.scale(dpr,dpr);
    const pad={l:58,r:18,t:22,b:35}; const values=[];
    defs.forEach(d=>rows.forEach(r=>{const v=Number(r[d.key]);if(Number.isFinite(v))values.push(v)})); if(!values.length)return;
    let min=Math.min(...values), max=Math.max(...values); if(min===max){min-=1;max+=1} const span=max-min; min-=span*.08; max+=span*.08;
    const x=i=>pad.l+(w-pad.l-pad.r)*(rows.length===1 ? 0.5 : i/(rows.length-1)); const y=v=>pad.t+(h-pad.t-pad.b)*(1-(v-min)/(max-min));
    ctx.font='11px system-ui'; ctx.fillStyle=css('--muted','#6d7a86'); ctx.strokeStyle=css('--line','#d9e0e6'); ctx.lineWidth=1;
    for(let i=0;i<4;i++){const yy=pad.t+(h-pad.t-pad.b)*i/3;ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();const val=max-(max-min)*i/3;ctx.fillText(percent?`${val.toFixed(1)}%`:compact(val),5,yy+4)}
    rows.forEach((r,i)=>{if(i%Math.max(1,Math.ceil(rows.length/6))===0||i===rows.length-1){ctx.fillText(String(r.label||''),Math.max(pad.l,x(i)-18),h-10)}});
    defs.forEach(d=>{ctx.strokeStyle=d.color;ctx.lineWidth=2.2;ctx.setLineDash(d.dash?[6,5]:[]);ctx.beginPath();let started=false;rows.forEach((r,i)=>{const v=Number(r[d.key]);if(!Number.isFinite(v))return;const xx=x(i),yy=y(v);if(!started){ctx.moveTo(xx,yy);started=true}else ctx.lineTo(xx,yy)});ctx.stroke();ctx.setLineDash([]);});
    let lx=pad.l; defs.forEach(d=>{ctx.fillStyle=d.color;ctx.fillRect(lx,pad.t-14,16,3);ctx.fillStyle=css('--muted','#6d7a86');ctx.fillText(d.label,lx+21,pad.t-9);lx+=ctx.measureText(d.label).width+48});
  }
  const primary=css('--primary','#3a6f99'), secondary='#7b96ad', accent='#5f8a86';
  const renderCharts=()=>{
    document.querySelectorAll('canvas[data-mf-chart="numbers-scale"]').forEach(c=>lineChart(c,parse(c),[{key:'revenue',label:'Revenue',color:primary},{key:'fcf',label:'FCF',color:accent},{key:'forecast_revenue',label:'Revenue forecast',color:primary,dash:true}],false));
    document.querySelectorAll('canvas[data-mf-chart="numbers-margin"]').forEach(c=>lineChart(c,parse(c),[{key:'op_margin',label:'Operating margin',color:primary},{key:'fcf_margin',label:'FCF margin',color:accent},{key:'forecast_op_margin',label:'Op margin forecast',color:primary,dash:true}],true));
    document.querySelectorAll('canvas[data-mf-chart="working-capital"]').forEach(c=>lineChart(c,parse(c),[{key:'inventory',label:'Inventory',color:primary},{key:'receivables',label:'Receivables',color:secondary}],false));
    document.querySelectorAll('canvas[data-mf-chart="tape-price"]').forEach(c=>lineChart(c,parse(c),[{key:'price',label:'Price',color:primary}],false));
    document.querySelectorAll('canvas[data-mf-chart="tape-short"]').forEach(c=>lineChart(c,parse(c),[{key:'short_pct',label:'Daily short volume %',color:secondary}],true));
  };
  renderCharts();
  let resizeTimer=null;
  window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(renderCharts,180)});
})();
