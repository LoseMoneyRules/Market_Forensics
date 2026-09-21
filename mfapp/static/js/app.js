(() => {
  'use strict';

  const root = document.documentElement;
  const body = document.body;
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const realRole = document.querySelector('meta[name="mf-real-role"]')?.content || '';
  const effectiveRole = document.querySelector('meta[name="mf-effective-role"]')?.content || '';

  const themeButton = document.getElementById('mf-theme-toggle');
  function applyTheme(theme, {persist = true} = {}) {
    const next = theme === 'dark' ? 'dark' : 'light';
    root.dataset.theme = next;
    if (persist) {
      try { window.localStorage.setItem('mf-theme', next); } catch (_) {}
    }
    if (themeButton) themeButton.textContent = next === 'dark' ? 'Light' : 'Dark';
    window.dispatchEvent(new CustomEvent('mf-theme-change', {detail: {theme: next}}));
  }
  applyTheme(root.dataset.theme || 'light', {persist: false});
  themeButton?.addEventListener('click', () => applyTheme(root.dataset.theme === 'dark' ? 'light' : 'dark'));

  document.querySelectorAll('.flash').forEach((el) => {
    window.setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateY(-4px)'; }, 4500);
    window.setTimeout(() => el.remove(), 5000);
  });

  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-copy]');
    if (!button) return;
    const input = document.querySelector(button.dataset.copy);
    if (!input || !navigator.clipboard) return;
    navigator.clipboard.writeText(input.value).then(() => {
      const old = button.textContent;
      button.textContent = 'Copied';
      window.setTimeout(() => { button.textContent = old; }, 1400);
    });
  });

  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-confirm]');
    if (!trigger) return;
    if (!window.confirm(trigger.dataset.confirm || 'Continue?')) event.preventDefault();
  });

  document.addEventListener('submit', (event) => {
    const form = event.target.closest?.('form[data-confirm]');
    if (!form) return;
    if (!window.confirm(form.dataset.confirm || 'Continue?')) event.preventDefault();
  });

  document.addEventListener('change', (event) => {
    const control = event.target.closest?.('[data-submit-on-change]');
    if (!control?.form) return;
    control.form.requestSubmit();
  });

  // One mobile-navigation controller. No later script is allowed to rebind it.
  const menuButton = document.getElementById('mf-mobile-menu');
  const toolsButton = document.getElementById('mf-mobile-tools');
  const backdrop = document.getElementById('mf-mobile-backdrop');
  const nav = document.getElementById('mf-primary-nav');
  const tools = document.getElementById('mf-top-actions');

  function setMobile(which, open) {
    const navOpen = which === 'nav' && open;
    const toolsOpen = which === 'tools' && open;
    body.classList.toggle('nav-open', navOpen);
    body.classList.toggle('tools-open', toolsOpen);
    menuButton?.setAttribute('aria-expanded', navOpen ? 'true' : 'false');
    toolsButton?.setAttribute('aria-expanded', toolsOpen ? 'true' : 'false');
    backdrop?.setAttribute('aria-hidden', (navOpen || toolsOpen) ? 'false' : 'true');
  }
  function closeMobile() { setMobile('', false); }
  menuButton?.addEventListener('click', () => setMobile('nav', !body.classList.contains('nav-open')));
  toolsButton?.addEventListener('click', () => setMobile('tools', !body.classList.contains('tools-open')));
  backdrop?.addEventListener('click', closeMobile);
  nav?.addEventListener('click', (event) => { if (event.target.closest('a')) closeMobile(); });
  tools?.addEventListener('click', (event) => { if (event.target.closest('a,button[type="submit"]')) closeMobile(); });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') closeMobile(); });
  window.addEventListener('resize', () => { if (window.innerWidth > 900) closeMobile(); });

  // Mobile research-step selector. The server owns markup; JavaScript owns state.
  document.querySelectorAll('[data-company-tabs-toggle]').forEach((toggle) => {
    const id = toggle.getAttribute('aria-controls');
    const tabs = id ? document.getElementById(id) : null;
    if (!tabs) return;
    const close = () => {
      tabs.classList.remove('mobile-open');
      toggle.setAttribute('aria-expanded', 'false');
    };
    toggle.addEventListener('click', () => {
      const open = !tabs.classList.contains('mobile-open');
      tabs.classList.toggle('mobile-open', open);
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    tabs.addEventListener('click', (event) => { if (event.target.closest('a')) close(); });
  });

  const valuationForm = document.getElementById('valuation-model-form');
  valuationForm?.addEventListener('submit', () => {
    valuationForm.querySelectorAll('[data-percent="1"]').forEach((input) => {
      const value = Number.parseFloat(input.value);
      if (Number.isFinite(value)) input.value = String(value / 100);
    });
  });

  // Localize visible server timestamps while keeping UTC in storage.
  const isoLike = /\b(20\d{2}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::\d{2})?\b/g;
  document.querySelectorAll('main time[data-utc], main [data-local-time]').forEach((el) => {
    const raw = el.getAttribute('datetime') || el.dataset.utc || el.textContent.trim();
    const normalized = /Z$|[+-]\d\d:\d\d$/.test(raw) ? raw : raw.replace(' ', 'T') + 'Z';
    const d = new Date(normalized);
    if (!Number.isNaN(d.getTime())) el.textContent = d.toLocaleString();
  });
  document.querySelectorAll('main td, main small, main p, main strong').forEach((el) => {
    if (el.children.length) return;
    const value = el.textContent || '';
    if (!isoLike.test(value)) { isoLike.lastIndex = 0; return; }
    isoLike.lastIndex = 0;
    el.textContent = value.replace(isoLike, (all, d, t) => {
      const parsed = new Date(d + 'T' + t + ':00Z');
      return Number.isNaN(parsed.getTime()) ? all : parsed.toLocaleString([], {year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
    });
  });

  // Keep company identity visible after the main company header scrolls away.
  const topbarSecurity = document.getElementById('mf-topbar-security');
  const companyHeader = document.querySelector('.company-head-standard');
  function syncTopbarSecurity() {
    if (!topbarSecurity || !companyHeader) return;
    const topbar = document.querySelector('.topbar');
    const threshold = topbar?.getBoundingClientRect().height || 64;
    const visible = companyHeader.getBoundingClientRect().bottom <= threshold + 4;
    topbarSecurity.classList.toggle('visible', visible);
    body.classList.toggle('security-context-visible', visible);
  }
  if (topbarSecurity && companyHeader) {
    syncTopbarSecurity();
    window.addEventListener('scroll', syncTopbarSecurity, {passive:true});
    window.addEventListener('resize', syncTopbarSecurity);
  }

  // Live market reference.
  const ticker = document.querySelector('.ticker-badge')?.textContent?.trim();
  const priceNode = document.querySelector('[data-live-price]');
  const topbarPriceNodes = document.querySelectorAll('[data-topbar-live-price]');
  const priceMeta = document.querySelector('[data-live-price-meta]');
  const marketBox = priceNode?.closest('.company-market');
  function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
  }
  function showQuoteUnavailable(message = 'Live quote unavailable') {
    if (!priceMeta) return;
    priceMeta.textContent = message;
    priceMeta.dataset.semantic = 'caution';
  }
  async function readQuote() {
    if (!ticker) return null;
    try {
      const response = await fetch('/company/' + encodeURIComponent(ticker) + '/price/live', {credentials:'same-origin',headers:{Accept:'application/json'}});
      if (!response.ok) {
        showQuoteUnavailable();
        return null;
      }
      const q = await response.json();
      if (Number.isFinite(Number(q.price))) {
        const formattedPrice = '$' + Number(q.price).toFixed(2);
        if (priceNode) priceNode.textContent = formattedPrice;
        topbarPriceNodes.forEach((node)=>{ node.textContent = formattedPrice; });
      }
      if (priceMeta) {
        priceMeta.textContent = '';
        delete priceMeta.dataset.semantic;
        const dot = document.createElement('i');
        dot.className = 'live-dot' + (q.fresh ? '' : ' stale');
        priceMeta.append(dot, document.createTextNode((q.provider || 'quote') + ' · ' + fmtTime(q.as_of)));
      }
      return q;
    } catch (_) {
      showQuoteUnavailable();
      return null;
    }
  }
  async function requestQuote() {
    if (!ticker || !csrf || !location.pathname.includes('/company/')) return;
    const current = await readQuote();
    if (current?.fresh) return;
    marketBox?.classList.add('refreshing');
    try {
      const response = await fetch('/company/' + encodeURIComponent(ticker) + '/price/refresh', {method:'POST',credentials:'same-origin',headers:{Accept:'application/json','X-CSRFToken':csrf}});
      if (response.ok) {
        const state = await response.json();
        if (state?.status === 'COOLDOWN') marketBox?.classList.remove('refreshing');
      } else {
        showQuoteUnavailable('Live quote refresh unavailable');
      }
      window.setTimeout(readQuote, 2500);
      window.setTimeout(readQuote, 9000);
    } catch (_) {
      showQuoteUnavailable('Live quote refresh unavailable');
    } finally {
      window.setTimeout(() => marketBox?.classList.remove('refreshing'), 3000);
    }
  }
  if (ticker && location.pathname.includes('/company/')) {
    window.setTimeout(requestQuote, 1600);
    window.setInterval(requestQuote, 5 * 60 * 1000);
  }

  // Canvas charts.
  function parseData(node, key) {
    try { return JSON.parse(node.dataset[key || 'series'] || '[]'); } catch (_) { return []; }
  }
  function css(name, fallback) { return getComputedStyle(root).getPropertyValue(name).trim() || fallback; }
  function compact(v) {
    const num = Number(v);
    if (!Number.isFinite(num)) return '—';
    const a = Math.abs(num);
    if (a >= 1e12) return (num/1e12).toFixed(1) + 'T';
    if (a >= 1e9) return (num/1e9).toFixed(1) + 'B';
    if (a >= 1e6) return (num/1e6).toFixed(1) + 'M';
    if (a >= 1e3) return (num/1e3).toFixed(1) + 'K';
    return num.toFixed(1);
  }
  function lineChart(canvas, rows, defs, percent) {
    if (!canvas || !rows.length) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(300, rect.width), h = Math.max(190, rect.height || 230);
    canvas.width = w * dpr; canvas.height = h * dpr;
    const ctx = canvas.getContext('2d'); ctx.scale(dpr,dpr);
    const pad = {l:58,r:18,t:24,b:35};
    const values = [];
    defs.forEach((d) => rows.forEach((row) => { const v=Number(row[d.key]); if(Number.isFinite(v)) values.push(v); }));
    if (!values.length) return;
    let min=Math.min.apply(null,values), max=Math.max.apply(null,values);
    if(min===max){min-=1;max+=1}
    const span=max-min; min-=span*.08; max+=span*.08;
    const x=(i)=>pad.l+(w-pad.l-pad.r)*(rows.length===1?.5:i/(rows.length-1));
    const y=(v)=>pad.t+(h-pad.t-pad.b)*(1-(v-min)/(max-min));
    ctx.font='13px system-ui'; ctx.fillStyle=css('--muted','#6d7a86'); ctx.strokeStyle=css('--line','#d9e0e6'); ctx.lineWidth=1;
    for(let i=0;i<4;i++){const yy=pad.t+(h-pad.t-pad.b)*i/3;ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();const val=max-(max-min)*i/3;ctx.fillText(percent?val.toFixed(1)+'%':compact(val),5,yy+4)}
    rows.forEach((row,i)=>{if(i%Math.max(1,Math.ceil(rows.length/6))===0||i===rows.length-1)ctx.fillText(String(row.label||row.date||''),Math.max(pad.l,x(i)-18),h-10)});
    defs.forEach((d)=>{ctx.strokeStyle=d.color;ctx.lineWidth=d.width||2.2;ctx.setLineDash(d.dash?[6,5]:[]);ctx.beginPath();let started=false;rows.forEach((row,i)=>{const v=Number(row[d.key]);if(!Number.isFinite(v))return;const xx=x(i),yy=y(v);if(!started){ctx.moveTo(xx,yy);started=true}else ctx.lineTo(xx,yy)});ctx.stroke();ctx.setLineDash([]);});
  }
  function barChart(canvas, rows, key, opts={}) {
    rows=(rows||[]).filter(row=>optionalNumber(row[key])!==null);
    if(!canvas||!rows.length)return;
    const rect=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
    const w=Math.max(320,rect.width),h=Math.max(190,rect.height||230),pad={l:64,r:18,t:24,b:38};
    canvas.width=w*dpr;canvas.height=h*dpr;
    const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
    const values=rows.map(row=>optionalNumber(row[key])).filter(v=>v!==null);
    let min=Math.min(0,...values),max=Math.max(0,...values);
    if(min===max){max=min+1}
    const span=max-min||1;min-=span*.06;max+=span*.06;
    const plotW=w-pad.l-pad.r,plotH=h-pad.t-pad.b,slot=plotW/rows.length,barW=Math.max(2,Math.min(18,slot*.68));
    const x=i=>pad.l+slot*i+slot/2;
    const y=v=>pad.t+plotH*(1-(v-min)/(max-min));
    const zero=y(0);
    ctx.font='13px system-ui';ctx.strokeStyle=css('--mf-chart-grid','#d9e0e6');ctx.fillStyle=css('--mf-chart-text','#4f6272');
    for(let i=0;i<4;i++){
      const yy=pad.t+plotH*i/3;ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();
      const val=max-(max-min)*i/3;ctx.fillText(opts.percent?val.toFixed(1)+'%':compact(val),4,yy+4);
    }
    rows.forEach((row,i)=>{
      const v=optionalNumber(row[key]);if(v===null)return;
      const yy=y(v),top=Math.min(zero,yy),height=Math.max(1,Math.abs(zero-yy));
      ctx.fillStyle=opts.signed?(v>=0?css('--mf-chart-bull','#1f7a54'):css('--mf-chart-bear','#a04444')):css('--mf-chart-price','#3a6f99');
      ctx.fillRect(x(i)-barW/2,top,barW,height);
      if(i%Math.max(1,Math.ceil(rows.length/6))===0||i===rows.length-1){
        ctx.fillStyle=css('--mf-chart-text','#4f6272');
        const raw=String(row.label||row.date||row.week_start||'');
        const label=/^\d{4}-\d{2}-\d{2}/.test(raw)?raw.slice(5):raw;
        ctx.fillText(label,Math.max(pad.l,x(i)-18),h-10);
      }
    });
  }

  function tapePriceFlowChart(canvas) {
    let prices=parseData(canvas,'priceSeries').filter(r=>optionalNumber(r.price)!==null&&r.date);
    const flows=parseData(canvas,'flowSeries').filter(r=>optionalNumber(r.cumulative_20d)!==null&&r.date);
    if(!prices.length&&!flows.length)return;
    if(flows.length){
      const first=String(flows[0].date);
      prices=prices.filter(r=>String(r.date)>=first);
    }
    const rect=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
    const w=Math.max(360,rect.width),h=Math.max(220,rect.height||280),pad={l:62,r:78,t:28,b:38};
    canvas.width=w*dpr;canvas.height=h*dpr;
    const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
    const dates=[...prices,...flows].map(r=>new Date(String(r.date)+'T00:00:00').getTime()).filter(Number.isFinite);
    if(!dates.length)return;
    let d0=Math.min(...dates),d1=Math.max(...dates);if(d0===d1)d1=d0+86400000;
    const pv=prices.map(r=>optionalNumber(r.price)).filter(v=>v!==null),fv=flows.map(r=>optionalNumber(r.cumulative_20d)).filter(v=>v!==null);
    let pmin=pv.length?Math.min(...pv):0,pmax=pv.length?Math.max(...pv):1,fmin=fv.length?Math.min(...fv):0,fmax=fv.length?Math.max(...fv):1;
    if(pmin===pmax){pmin-=1;pmax+=1}if(fmin===fmax){fmin-=1;fmax+=1}
    const ps=pmax-pmin,fs=fmax-fmin;pmin-=ps*.08;pmax+=ps*.08;fmin-=fs*.08;fmax+=fs*.08;
    const x=d=>pad.l+(w-pad.l-pad.r)*((new Date(String(d)+'T00:00:00').getTime()-d0)/(d1-d0));
    const yp=v=>pad.t+(h-pad.t-pad.b)*(1-(v-pmin)/(pmax-pmin));
    const yf=v=>pad.t+(h-pad.t-pad.b)*(1-(v-fmin)/(fmax-fmin));
    ctx.font='13px system-ui';ctx.strokeStyle=css('--mf-chart-grid','#d9e0e6');ctx.fillStyle=css('--mf-chart-text','#4f6272');
    for(let i=0;i<4;i++){
      const yy=pad.t+(h-pad.t-pad.b)*i/3;ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();
      ctx.fillText('$'+(pmax-(pmax-pmin)*i/3).toFixed(1),4,yy+4);
      ctx.textAlign='right';ctx.fillText(compact(fmax-(fmax-fmin)*i/3),w-5,yy+4);ctx.textAlign='left';
    }
    const draw=(rows,key,y,color,dash=false)=>{
      ctx.strokeStyle=color;ctx.lineWidth=2.4;ctx.setLineDash(dash?[6,4]:[]);ctx.beginPath();let started=false;
      rows.forEach(r=>{const v=optionalNumber(r[key]);if(v===null)return;const xx=x(r.date),yy=y(v);if(!started){ctx.moveTo(xx,yy);started=true}else ctx.lineTo(xx,yy)});
      ctx.stroke();ctx.setLineDash([]);
    };
    draw(prices,'price',yp,css('--mf-chart-price','#3a6f99'));
    draw(flows,'cumulative_20d',yf,css('--mf-chart-bull','#1f7a54'));
    ctx.fillStyle=css('--mf-chart-price','#3a6f99');ctx.fillText('Price · left',pad.l,pad.t-10);
    ctx.textAlign='right';ctx.fillStyle=css('--mf-chart-bull','#1f7a54');ctx.fillText('Cumulative Large Flow · right',w-pad.r,pad.t-10);ctx.textAlign='left';
  }

  function dualAxisWorkingCapitalChart(canvas, rows) {
    if (!canvas || !rows.length) return;
    const rect=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
    const w=Math.max(300,rect.width),h=Math.max(190,rect.height||230);
    canvas.width=w*dpr;canvas.height=h*dpr;
    const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
    const pad={l:68,r:68,t:28,b:35};
    const leftValues=rows.map(r=>optionalNumber(r.inventory)).filter(v=>v!==null);
    const rightValues=rows.map(r=>optionalNumber(r.receivables)).filter(v=>v!==null);
    if(!leftValues.length&&!rightValues.length)return;
    const bounds=(values)=>{
      if(!values.length)return [0,1];
      let min=Math.min(...values),max=Math.max(...values);
      if(min===max){const bump=Math.max(1,Math.abs(max)*.08);min-=bump;max+=bump}
      const span=max-min||1;return [min-span*.08,max+span*.08];
    };
    const [lmin,lmax]=bounds(leftValues),[rmin,rmax]=bounds(rightValues);
    const x=(i)=>pad.l+(w-pad.l-pad.r)*(rows.length===1?.5:i/(rows.length-1));
    const yl=(v)=>pad.t+(h-pad.t-pad.b)*(1-(v-lmin)/(lmax-lmin));
    const yr=(v)=>pad.t+(h-pad.t-pad.b)*(1-(v-rmin)/(rmax-rmin));
    ctx.font='13px system-ui';ctx.lineWidth=1;
    for(let i=0;i<4;i++){
      const yy=pad.t+(h-pad.t-pad.b)*i/3;
      ctx.strokeStyle=css('--line','#d9e0e6');ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();
      const lv=lmax-(lmax-lmin)*i/3,rv=rmax-(rmax-rmin)*i/3;
      ctx.fillStyle=css('--mf-chart-price','#3a6f99');ctx.textAlign='left';ctx.fillText(compact(lv),5,yy+4);
      ctx.fillStyle=css('--mf-chart-secondary','#6b7f91');ctx.textAlign='right';ctx.fillText(compact(rv),w-5,yy+4);
    }
    ctx.fillStyle=css('--muted','#6d7a86');ctx.textAlign='left';
    rows.forEach((row,i)=>{if(i%Math.max(1,Math.ceil(rows.length/6))===0||i===rows.length-1)ctx.fillText(String(row.label||''),Math.max(pad.l,x(i)-18),h-10)});
    const draw=(key,y,color)=>{
      ctx.strokeStyle=color;ctx.lineWidth=2.3;ctx.beginPath();let started=false;
      rows.forEach((row,i)=>{const v=optionalNumber(row[key]);if(v===null)return;const xx=x(i),yy=y(v);if(!started){ctx.moveTo(xx,yy);started=true}else ctx.lineTo(xx,yy)});
      ctx.stroke();
      rows.forEach((row,i)=>{const v=optionalNumber(row[key]);if(v===null)return;ctx.beginPath();ctx.arc(x(i),y(v),2.4,0,Math.PI*2);ctx.fillStyle=color;ctx.fill()});
    };
    const inventoryColor=css('--mf-chart-price','#3a6f99'),receivablesColor=css('--mf-chart-secondary','#6b7f91');
    draw('inventory',yl,inventoryColor);draw('receivables',yr,receivablesColor);
    ctx.font='13px system-ui';ctx.textAlign='left';ctx.fillStyle=inventoryColor;ctx.fillText('Inventory · left scale',pad.l,pad.t-10);
    ctx.textAlign='right';ctx.fillStyle=receivablesColor;ctx.fillText('Receivables · right scale',w-pad.r,pad.t-10);ctx.textAlign='left';
  }

  function revenueFcfChart(canvas, rows) {
    rows=(rows||[]).filter(row=>optionalNumber(row.revenue)!==null || optionalNumber(row.forecast_revenue)!==null || optionalNumber(row.fcf)!==null);
    if(!canvas||!rows.length)return;
    const rect=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
    const w=Math.max(360,rect.width),h=Math.max(220,rect.height||270),pad={l:62,r:70,t:30,b:42};
    canvas.width=w*dpr;canvas.height=h*dpr;
    const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
    const revVals=rows.map(r=>optionalNumber(r.revenue)??optionalNumber(r.forecast_revenue)).filter(v=>v!==null);
    const fcfVals=rows.map(r=>optionalNumber(r.fcf)).filter(v=>v!==null);
    if(!revVals.length&&!fcfVals.length)return;
    let revMax=Math.max(1,...revVals.map(v=>Math.max(0,v)));
    let fMin=fcfVals.length?Math.min(0,...fcfVals):0,fMax=fcfVals.length?Math.max(1,...fcfVals):1;
    revMax*=1.12;
    if(fMin===fMax){fMin-=1;fMax+=1}
    const fSpan=fMax-fMin;fMin-=fSpan*.08;fMax+=fSpan*.08;
    const plotW=w-pad.l-pad.r,plotH=h-pad.t-pad.b,slot=plotW/rows.length,barW=Math.max(8,Math.min(42,slot*.58));
    const x=i=>pad.l+slot*i+slot/2;
    const yRev=v=>pad.t+plotH*(1-Math.max(0,v)/revMax);
    const yFcf=v=>pad.t+plotH*(1-(v-fMin)/(fMax-fMin));
    ctx.font='13px system-ui';ctx.strokeStyle=css('--mf-chart-grid','#d9e0e6');ctx.fillStyle=css('--mf-chart-text','#4f6272');ctx.lineWidth=1;
    for(let i=0;i<4;i++){
      const yy=pad.t+plotH*i/3;ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();
      ctx.fillText(compact(revMax*(1-i/3)),4,yy+4);
      ctx.textAlign='right';ctx.fillText(compact(fMax-(fMax-fMin)*i/3),w-5,yy+4);ctx.textAlign='left';
    }
    rows.forEach((row,i)=>{
      const hist=optionalNumber(row.revenue),forecast=optionalNumber(row.forecast_revenue),rev=hist??forecast;
      if(rev!==null){
        const yy=yRev(rev),base=pad.t+plotH;
        ctx.globalAlpha=forecast!==null&&hist===null?.45:.88;
        ctx.fillStyle=css('--mf-chart-price','#3a6f99');
        ctx.fillRect(x(i)-barW/2,yy,barW,Math.max(1,base-yy));
        ctx.globalAlpha=1;
      }
      if(i%Math.max(1,Math.ceil(rows.length/7))===0||i===rows.length-1){
        ctx.fillStyle=css('--mf-chart-text','#4f6272');
        ctx.fillText(String(row.label||''),Math.max(pad.l,x(i)-18),h-12);
      }
    });
    let started=false;ctx.strokeStyle=css('--mf-chart-bull','#1f7a54');ctx.lineWidth=2.5;ctx.beginPath();
    rows.forEach((row,i)=>{
      const value=optionalNumber(row.fcf);if(value===null){started=false;return}
      const xx=x(i),yy=yFcf(value);if(!started){ctx.moveTo(xx,yy);started=true}else ctx.lineTo(xx,yy);
    });ctx.stroke();
    rows.forEach((row,i)=>{const value=optionalNumber(row.fcf);if(value===null)return;ctx.beginPath();ctx.arc(x(i),yFcf(value),2.6,0,Math.PI*2);ctx.fillStyle=css('--mf-chart-bull','#1f7a54');ctx.fill()});
    ctx.fillStyle=css('--mf-chart-price','#3a6f99');ctx.fillText('Revenue · columns',pad.l,pad.t-10);
    ctx.textAlign='right';ctx.fillStyle=css('--mf-chart-bull','#1f7a54');ctx.fillText('FCF · line',w-pad.r,pad.t-10);ctx.textAlign='left';
  }

  function optionalNumber(value) {
    if (value === null || value === undefined || value === '') return null;
    const out=Number(value);
    return Number.isFinite(out) ? out : null;
  }
  function valuationChart(canvas) {
    const history=parseData(canvas,'history').filter((row)=>optionalNumber(row.price)!==null);
    const levels=(() => { try { return JSON.parse(canvas.dataset.levels||'{}'); } catch (_) { return {}; } })();
    const bear=optionalNumber(levels.bear),base=optionalNumber(levels.base),bull=optionalNumber(levels.bull),current=optionalNumber(levels.current);
    const scenarioDefs=[
      bear!==null?{key:'bear',label:'Bear',color:css('--mf-chart-bear','#a13b3b'),dash:true}:null,
      base!==null?{key:'base',label:'Base',color:css('--mf-chart-price','#3a6f99'),dash:true}:null,
      bull!==null?{key:'bull',label:'Bull',color:css('--mf-chart-bull','#1f7a54'),dash:true}:null,
    ].filter(Boolean);
    if(!history.length && current===null && !scenarioDefs.length) return;
    const rows=history.length
      ? history.map((row)=>({date:String(row.date).slice(0,7),price:optionalNumber(row.price),bear,base,bull}))
      : [{label:'Current',price:current,bear,base,bull},{label:'Scenario',price:current,bear,base,bull}];
    const defs=[current!==null||history.length?{key:'price',label:'Market',color:css('--mf-chart-market','#7f8a94'),width:2.4}:null,...scenarioDefs].filter(Boolean);
    lineChart(canvas,rows,defs,false);
  }
  function tapePriceShortChart(canvas) {
    const prices=parseData(canvas,'priceSeries').filter(r=>optionalNumber(r.price)!==null && r.date);
    const shorts=parseData(canvas,'shortSeries').filter(r=>optionalNumber(r.short)!==null && r.date);
    if(!prices.length && !shorts.length)return;
    const rect=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
    const w=Math.max(360,rect.width),h=Math.max(220,rect.height||300),pad={l:58,r:72,t:26,b:36};
    canvas.width=w*dpr; canvas.height=h*dpr;
    const ctx=canvas.getContext('2d'); ctx.scale(dpr,dpr);
    const allDates=[...prices,...shorts].map(r=>new Date(String(r.date)+'T00:00:00').getTime()).filter(Number.isFinite);
    if(!allDates.length)return;
    let d0=Math.min(...allDates),d1=Math.max(...allDates); if(d0===d1)d1=d0+86400000;
    const pVals=prices.map(r=>optionalNumber(r.price)).filter(v=>v!==null),sVals=shorts.map(r=>optionalNumber(r.short)).filter(v=>v!==null);
    let pMin=pVals.length?Math.min(...pVals):0,pMax=pVals.length?Math.max(...pVals):1,sMin=sVals.length?Math.min(...sVals):0,sMax=sVals.length?Math.max(...sVals):1;
    if(pMin===pMax){pMin-=Math.max(1,pMin*.05);pMax+=Math.max(1,pMax*.05)}
    if(sMin===sMax){sMin=Math.max(0,sMin*.95);sMax=Math.max(1,sMax*1.05)}
    const pSpan=Math.max(.01,pMax-pMin),sSpan=Math.max(1,sMax-sMin);
    pMin-=pSpan*.08;pMax+=pSpan*.08;sMin=Math.max(0,sMin-sSpan*.08);sMax+=sSpan*.08;
    const x=d=>pad.l+(w-pad.l-pad.r)*((new Date(String(d)+'T00:00:00').getTime()-d0)/(d1-d0));
    const yp=v=>pad.t+(h-pad.t-pad.b)*(1-(v-pMin)/(pMax-pMin));
    const ys=v=>pad.t+(h-pad.t-pad.b)*(1-(v-sMin)/(sMax-sMin));
    ctx.font='13px system-ui';ctx.fillStyle=css('--mf-chart-text','#4f6272');ctx.strokeStyle=css('--mf-chart-grid','#d9e0e6');ctx.lineWidth=1;
    for(let i=0;i<4;i++){
      const yy=pad.t+(h-pad.t-pad.b)*i/3;
      ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();
      ctx.fillText('$'+(pMax-(pMax-pMin)*i/3).toFixed(1),5,yy+4);
      const sv=sMax-(sMax-sMin)*i/3;ctx.textAlign='right';ctx.fillText(compact(sv),w-5,yy+4);ctx.textAlign='left';
    }
    ctx.strokeStyle=css('--mf-chart-price','#3a6f99');ctx.lineWidth=2.5;ctx.beginPath();
    prices.forEach((r,i)=>{const xx=x(r.date),yy=yp(Number(r.price));i?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy)});ctx.stroke();
    if(shorts.length){
      ctx.strokeStyle=css('--mf-chart-bear','#a04444');ctx.lineWidth=2;ctx.setLineDash([6,4]);ctx.beginPath();
      shorts.forEach((r,i)=>{const xx=x(r.date),yy=ys(Number(r.short));i?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy)});ctx.stroke();ctx.setLineDash([]);
      shorts.forEach(r=>{ctx.beginPath();ctx.arc(x(r.date),ys(Number(r.short)),2.8,0,Math.PI*2);ctx.fillStyle=css('--mf-chart-bear','#a04444');ctx.fill()});
    }
    const marks=[d0,d0+(d1-d0)/2,d1];ctx.fillStyle=css('--mf-chart-text','#4f6272');
    marks.forEach((d,i)=>{const label=new Date(d).toLocaleDateString([],{month:'short',year:'2-digit'});const xx=pad.l+(w-pad.l-pad.r)*i/2;ctx.fillText(label,Math.max(pad.l,Math.min(w-pad.r-30,xx-16)),h-10)});
    ctx.fillStyle=css('--mf-chart-price','#3a6f99');ctx.fillText('Price',pad.l,pad.t-10);
    ctx.textAlign='right';ctx.fillStyle=css('--mf-chart-bear','#a04444');ctx.fillText('Short interest',w-pad.r,pad.t-10);ctx.textAlign='left';
  }
  const primary=css('--primary','#3a6f99'), secondary='#7b96ad', accent='#5f8a86';
  function renderCharts(){
    document.querySelectorAll('canvas[data-mf-chart="numbers-scale"]').forEach((node)=>revenueFcfChart(node,parseData(node)));
    document.querySelectorAll('canvas[data-mf-chart="numbers-margin"]').forEach((node)=>lineChart(node,parseData(node),[{key:'op_margin',label:'Operating margin',color:primary},{key:'fcf_margin',label:'FCF margin',color:accent},{key:'forecast_op_margin',label:'Op margin forecast',color:primary,dash:true}],true));
    document.querySelectorAll('canvas[data-mf-chart="working-capital"]').forEach((node)=>dualAxisWorkingCapitalChart(node,parseData(node)));
    document.querySelectorAll('canvas[data-mf-chart="tape-price-flow"]').forEach(tapePriceFlowChart);
    document.querySelectorAll('canvas[data-mf-chart="tape-volume"]').forEach((node)=>barChart(node,parseData(node),'volume'));
    document.querySelectorAll('canvas[data-mf-chart="tape-price-short"]').forEach(tapePriceShortChart);
    document.querySelectorAll('canvas[data-mf-chart="tape-short"]').forEach((node)=>lineChart(node,parseData(node),[{key:'short_pct',label:'Daily short volume %',color:secondary}],true));
    document.querySelectorAll('canvas[data-mf-chart="tape-flow"]').forEach((node)=>barChart(node,parseData(node),'net_large',{signed:true}));
    document.querySelectorAll('canvas[data-mf-chart="tape-cumulative-flow"]').forEach((node)=>lineChart(node,parseData(node),[{key:'cumulative_5d',label:'5D Large Flow',color:primary},{key:'cumulative_20d',label:'20D Large Flow',color:accent}],false));
    document.querySelectorAll('canvas[data-mf-chart="tape-scores"]').forEach((node)=>lineChart(node,parseData(node),[{key:'absorption',label:'Absorption',color:accent},{key:'short_pressure',label:'Short Pressure',color:css('--mf-chart-bear','#a04444')},{key:'net_tape',label:'Net Tape',color:primary}],false));
    document.querySelectorAll('canvas[data-mf-chart="tape-whale"]').forEach((node)=>barChart(node,parseData(node),'net_whale',{signed:true}));
    document.querySelectorAll('canvas[data-mf-chart="tape-ats"]').forEach((node)=>lineChart(node,parseData(node).map(r=>({...r,label:r.week_start||r.date||''})),[{key:'ats_share_pct',label:'ATS share %',color:secondary}],true));
    document.querySelectorAll('canvas[data-mf-chart="valuation"]').forEach(valuationChart);
  }
  renderCharts();

  // Historical-price backfills are heavy background work, but their stored rows
  // should appear in Valuation without forcing a page reload.
  const valuationHistoryCanvas=document.querySelector('canvas[data-mf-chart="valuation"]');
  if(valuationHistoryCanvas && !parseData(valuationHistoryCanvas,'history').length && ticker){
    let priceHistoryPolls=0;
    const pollPriceHistory=async()=>{
      priceHistoryPolls+=1;
      try{
        const response=await fetch('/company/'+encodeURIComponent(ticker)+'/price/history/live',{credentials:'same-origin',headers:{Accept:'application/json'},cache:'no-store'});
        if(!response.ok)throw new Error('history '+response.status);
        const payload=await response.json();
        const state=document.querySelector('[data-price-history-state]');
        const job=document.querySelector('[data-price-history-job]');
        const note=document.querySelector('[data-price-history-note]');
        if(state){
          const span=(payload.first_date&&payload.last_date)?' · '+payload.first_date+' → '+payload.last_date:'';
          const provider=payload.provider?' · '+payload.provider:'';
          state.textContent=String(payload.count||0)+' plotted points'+span+provider;
        }
        if(job && payload.job?.id) job.textContent='Job #'+payload.job.id+' · '+(payload.job.status||'');
        if(Array.isArray(payload.rows)&&payload.rows.length){
          valuationHistoryCanvas.dataset.history=JSON.stringify(payload.rows);
          valuationChart(valuationHistoryCanvas);
          if(note)note.remove();
          return;
        }
        if(payload.job?.status==='FAILED'){
          if(note)note.textContent='Historical price backfill failed: '+(payload.job.error||'provider unavailable')+'. Use Refresh 2Y price history to retry.';
          return;
        }
        if(priceHistoryPolls<30)window.setTimeout(pollPriceHistory,4000);
      }catch(_){
        if(priceHistoryPolls<15)window.setTimeout(pollPriceHistory,5000);
      }
    };
    window.setTimeout(pollPriceHistory,2000);
  }
  let resizeTimer=null;
  window.addEventListener('resize',()=>{window.clearTimeout(resizeTimer);resizeTimer=window.setTimeout(renderCharts,180)});
  window.addEventListener('mf-theme-change',()=>window.setTimeout(renderCharts,30));

  // Central semantic status contract. Components expose meaning; CSS owns color.
  const semanticGroups = {
    positive: ['POSITIVE','GOOD','ATTRACTIVE','FAVORABLE','SUPPORTIVE','MET','PASS','STRENGTH','STRONG','SOUND','CLEAN','FRESH','AVAILABLE','VERIFIED','HEALTHY','CURRENT','BULLISH','LONG','READY','APPROVED','DONE','COMPLETE','COMPLETED','SUCCESS','VALIDATED','PUBLIC','OK'],
    negative: ['NEGATIVE','BAD','EXPENSIVE','DEMANDING','HOSTILE','MISS','FAIL','WEAKNESS','RED FLAG','RED FLAGS','FRAGILE','BLOCKER','CRITICAL','UNHEALTHY','BROKEN','BEARISH','SHORT','FAILED','ERROR','DETERIORATING'],
    caution: ['MIXED','UNCLEAR','PENDING','WATCH','WAIT','WARNING','DATA WARNING','PROVISIONAL','STALE','PARTIAL','INCOMPLETE','NEEDS REFRESH','UNDER REVIEW','LIMITED','REVIEW','MISSING EVIDENCE','PENDING APPROVAL','UNRESOLVED','INSUFFICIENT EVIDENCE','UNKNOWN','LOW DATA','NEEDS EVIDENCE'],
    info: ['RUNNING','QUEUED','INFO','SYSTEM','VALIDATION','CHECKING','LOCKED'],
    neutral: ['NEUTRAL','FAIR','BALANCED','IN LINE','UNRATED','OBSERVED','LATERAL','SIDEWAYS','NO DATA','NOT RUN','NOT STARTED','HOLD'],
    cancelled: ['CANCELLED','SUPERSEDED']
  };
  function semanticStatus(value) {
    const valueText=String(value||'').trim().toUpperCase().replaceAll('_',' ');
    if(!valueText)return'neutral';
    for(const [group,tokens] of Object.entries(semanticGroups)){
      if(tokens.some(token=>valueText===token||valueText.startsWith(token+' ')||valueText.endsWith(' '+token)))return group;
    }
    return 'neutral';
  }
  function applySemanticStatuses(scope=document) {
    scope.querySelectorAll?.('.status-chip,.gate-status,[data-status-value]').forEach((el)=>{
      const value=el.dataset.statusValue||el.textContent||'';
      el.dataset.semantic=semanticStatus(value);
    });
    scope.querySelectorAll?.('.lens-card').forEach((el)=>{
      el.dataset.semantic=semanticStatus(el.querySelector('strong')?.textContent||'');
    });
  }
  applySemanticStatuses();

  // Lightweight Research Control mutations update immediately. These are simple
  // database state changes, not background analytical jobs.
  document.addEventListener('submit', async (event) => {
    const form = event.target.closest?.('.gate-approval-form');
    if (!form) return;
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const prior = button?.textContent || '';
    if (button) { button.disabled = true; button.textContent = 'Saving…'; }
    const formData = new FormData(form);
    const endpoint = form.getAttribute('action');
    if (!endpoint) return;
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {Accept: 'application/json', 'X-CSRFToken': csrf},
        body: formData,
        cache: 'no-store'
      });
      const payload = await response.json().catch(()=>({}));
      if (!response.ok || !payload?.ok) throw new Error(payload?.message || ('gate '+response.status));
      const current = document.querySelector('[data-process-readiness]');
      if (!payload?.html || !current) throw new Error('gate response');
      const shell = document.createElement('div');
      shell.innerHTML = payload.html.trim();
      const next = shell.firstElementChild;
      if (!next) throw new Error('gate markup');
      current.replaceWith(next);
      applySemanticStatuses(next);
      if (payload?.research_conclusion) {
        document.querySelectorAll('[data-research-conclusion]').forEach((el)=>{
          el.textContent = payload.research_conclusion;
        });
      }
      dirty = false;
    } catch (error) {
      if (button) { button.disabled = false; button.textContent = prior; }
      const row = form.closest('.gate-row');
      let note = row?.querySelector('.gate-inline-error');
      if (!note && row) {
        note = document.createElement('small');
        note.className = 'gate-inline-error';
        row.appendChild(note);
      }
      if (note) note.textContent = error?.message || 'Unable to update readiness.';
    }
  });

  // CONTROL background-job observer + detached executor fallback.
  // Page requests stay fast: /jobs/pump only starts a separate CLI process and returns.
  const workerChip = document.getElementById('mf-worker-chip');
  if (realRole !== 'CONTROL' || effectiveRole !== 'CONTROL' || !csrf) return;
  const autoRefresh = document.querySelector('meta[name="mf-auto-refresh"]')?.content === '1';
  const pumpLockKey='mf-job-pump-kick-at';
  let busy=false, pumpBusy=false, stopped=false, timer=null, baselineFinished=null, dirty=false, refreshOffered=false;

  document.addEventListener('input',(event)=>{
    const target=event.target;
    if(target && (target.matches('input:not([type="hidden"]):not([type="search"]), textarea, select'))) dirty=true;
  },{capture:true});
  document.addEventListener('submit',()=>{dirty=false},{capture:true});

  function renderJobs(state){
    if(!workerChip)return;
    const queued=Number(state?.queued||0),running=Number(state?.running||0),failed=Number(state?.failed||0);
    const active=Array.isArray(state?.active_jobs)?state.active_jobs:[];
    const lead=active.find((row)=>row.status==='RUNNING')||active[0]||null;
    const target=lead?.target?String(lead.target):'';
    workerChip.title=active.length
      ? active.slice(0,6).map((row)=>'#'+row.id+' '+row.type+' · '+row.target+' · '+row.status).join('\n')
      : 'No active background jobs.';
    if(refreshOffered){workerChip.textContent='Data updated · refresh';workerChip.classList.add('job-updated');return}
    workerChip.classList.remove('job-updated');
    if(running)workerChip.textContent='Jobs · '+(target||'GLOBAL')+' · running · '+queued+' queued';
    else if(queued)workerChip.textContent='Jobs · '+(target||'GLOBAL')+' · queued · '+queued;
    else if(failed)workerChip.textContent='Jobs · idle · '+failed+' failed';
    else workerChip.textContent='Jobs · idle';
  }
  function schedule(ms){window.clearTimeout(timer);if(!stopped)timer=window.setTimeout(tick,ms)}
  async function status(){
    const r=await fetch('/jobs/status',{credentials:'same-origin',headers:{Accept:'application/json'},cache:'no-store'});
    if(r.status===401||r.status===403){stopped=true;return null}
    if(!r.ok)throw new Error('status '+r.status);
    return r.json();
  }
  async function kickExecutor(state){
    if(pumpBusy||Number(state?.due||0)<=0||Number(state?.running||0)>0)return false;
    const now=Date.now();
    let previous=0;
    try{previous=Number(window.localStorage.getItem(pumpLockKey)||0)}catch(_){}
    if(now-previous<12000)return false;
    try{window.localStorage.setItem(pumpLockKey,String(now))}catch(_){}
    pumpBusy=true;
    try{
      const r=await fetch('/jobs/pump',{
        method:'POST',credentials:'same-origin',
        headers:{Accept:'application/json','X-CSRFToken':csrf},
        cache:'no-store'
      });
      if(r.status===401||r.status===403){stopped=true;return false}
      if(!r.ok){
        if(workerChip){workerChip.textContent='Jobs · executor unavailable';workerChip.title='Background executor could not start.'}
        return false;
      }
      const payload=await r.json();
      if(payload?.spawned&&workerChip)workerChip.textContent='Jobs · starting background worker';
      return Boolean(payload?.spawned);
    }catch(_){
      if(workerChip)workerChip.textContent='Jobs · executor reconnecting';
      return false;
    }finally{pumpBusy=false}
  }
  function maybeRefresh(state){
    const finished=state?.last_finished_id??null;
    if(baselineFinished===null){baselineFinished=finished;return false}
    if(finished===null||finished===baselineFinished)return false;
    baselineFinished=finished;
    if(!autoRefresh)return false;
    if(ticker) readQuote();
    refreshOffered=true;
    if(workerChip){
      workerChip.title=dirty
        ? 'Background data finished. Unsaved edits are protected; click when ready to refresh.'
        : 'Background data finished. Click when you want to refresh the full research surface.';
      workerChip.style.cursor='pointer';
      workerChip.onclick=()=>window.location.reload();
    }
    return false;
  }
  async function tick(){
    if(busy||stopped)return;
    busy=true;
    try{
      const state=await status();if(!state)return;
      if(maybeRefresh(state))return;
      renderJobs(state);
      const kicked=await kickExecutor(state);
      const active=Number(state.running||0)+Number(state.queued||0);
      schedule(kicked?1200:(active?2500:8000));
    }catch(_){
      if(workerChip)workerChip.textContent='Jobs · reconnecting';
      schedule(12000);
    }finally{busy=false}
  }
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!busy&&!stopped)schedule(150)});
  schedule(250);

})(); + Number(q.price).toFixed(2);
        if (priceNode) priceNode.textContent = formattedPrice;
        topbarPriceNodes.forEach((node)=>{ node.textContent = formattedPrice; });
      }
      if (priceMeta) {
        priceMeta.textContent = '';
        delete priceMeta.dataset.semantic;
        const dot = document.createElement('i');
        dot.className = 'live-dot' + (q.fresh ? '' : ' stale');
        priceMeta.append(dot, document.createTextNode((q.provider || 'quote') + ' · ' + fmtTime(q.as_of)));
      }
      return q;
    } catch (_) {
      showQuoteUnavailable();
      return null;
    }
  }
  async function requestQuote() {
    if (!ticker || !csrf || !location.pathname.includes('/company/')) return;
    const current = await readQuote();
    if (current?.fresh) return;
    marketBox?.classList.add('refreshing');
    try {
      const response = await fetch('/company/' + encodeURIComponent(ticker) + '/price/refresh', {method:'POST',credentials:'same-origin',headers:{Accept:'application/json','X-CSRFToken':csrf}});
      if (response.ok) {
        const state = await response.json();
        if (state?.status === 'COOLDOWN') marketBox?.classList.remove('refreshing');
      } else {
        showQuoteUnavailable('Live quote refresh unavailable');
      }
      window.setTimeout(readQuote, 2500);
      window.setTimeout(readQuote, 9000);
    } catch (_) {
      showQuoteUnavailable('Live quote refresh unavailable');
    } finally {
      window.setTimeout(() => marketBox?.classList.remove('refreshing'), 3000);
    }
  }
  if (ticker && location.pathname.includes('/company/')) {
    window.setTimeout(requestQuote, 1600);
    window.setInterval(requestQuote, 5 * 60 * 1000);
  }

  // Canvas charts.
  function parseData(node, key) {
    try { return JSON.parse(node.dataset[key || 'series'] || '[]'); } catch (_) { return []; }
  }
  function css(name, fallback) { return getComputedStyle(root).getPropertyValue(name).trim() || fallback; }
  function compact(v) {
    const num = Number(v);
    if (!Number.isFinite(num)) return '—';
    const a = Math.abs(num);
    if (a >= 1e12) return (num/1e12).toFixed(1) + 'T';
    if (a >= 1e9) return (num/1e9).toFixed(1) + 'B';
    if (a >= 1e6) return (num/1e6).toFixed(1) + 'M';
    if (a >= 1e3) return (num/1e3).toFixed(1) + 'K';
    return num.toFixed(1);
  }
  function lineChart(canvas, rows, defs, percent) {
    if (!canvas || !rows.length) return;
    const rect = canvas.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(300, rect.width), h = Math.max(190, rect.height || 230);
    canvas.width = w * dpr; canvas.height = h * dpr;
    const ctx = canvas.getContext('2d'); ctx.scale(dpr,dpr);
    const pad = {l:58,r:18,t:24,b:35};
    const values = [];
    defs.forEach((d) => rows.forEach((row) => { const v=Number(row[d.key]); if(Number.isFinite(v)) values.push(v); }));
    if (!values.length) return;
    let min=Math.min.apply(null,values), max=Math.max.apply(null,values);
    if(min===max){min-=1;max+=1}
    const span=max-min; min-=span*.08; max+=span*.08;
    const x=(i)=>pad.l+(w-pad.l-pad.r)*(rows.length===1?.5:i/(rows.length-1));
    const y=(v)=>pad.t+(h-pad.t-pad.b)*(1-(v-min)/(max-min));
    ctx.font='13px system-ui'; ctx.fillStyle=css('--muted','#6d7a86'); ctx.strokeStyle=css('--line','#d9e0e6'); ctx.lineWidth=1;
    for(let i=0;i<4;i++){const yy=pad.t+(h-pad.t-pad.b)*i/3;ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();const val=max-(max-min)*i/3;ctx.fillText(percent?val.toFixed(1)+'%':compact(val),5,yy+4)}
    rows.forEach((row,i)=>{if(i%Math.max(1,Math.ceil(rows.length/6))===0||i===rows.length-1)ctx.fillText(String(row.label||row.date||''),Math.max(pad.l,x(i)-18),h-10)});
    defs.forEach((d)=>{ctx.strokeStyle=d.color;ctx.lineWidth=d.width||2.2;ctx.setLineDash(d.dash?[6,5]:[]);ctx.beginPath();let started=false;rows.forEach((row,i)=>{const v=Number(row[d.key]);if(!Number.isFinite(v))return;const xx=x(i),yy=y(v);if(!started){ctx.moveTo(xx,yy);started=true}else ctx.lineTo(xx,yy)});ctx.stroke();ctx.setLineDash([]);});
  }
  function barChart(canvas, rows, key, opts={}) {
    rows=(rows||[]).filter(row=>optionalNumber(row[key])!==null);
    if(!canvas||!rows.length)return;
    const rect=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
    const w=Math.max(320,rect.width),h=Math.max(190,rect.height||230),pad={l:64,r:18,t:24,b:38};
    canvas.width=w*dpr;canvas.height=h*dpr;
    const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
    const values=rows.map(row=>optionalNumber(row[key])).filter(v=>v!==null);
    let min=Math.min(0,...values),max=Math.max(0,...values);
    if(min===max){max=min+1}
    const span=max-min||1;min-=span*.06;max+=span*.06;
    const plotW=w-pad.l-pad.r,plotH=h-pad.t-pad.b,slot=plotW/rows.length,barW=Math.max(2,Math.min(18,slot*.68));
    const x=i=>pad.l+slot*i+slot/2;
    const y=v=>pad.t+plotH*(1-(v-min)/(max-min));
    const zero=y(0);
    ctx.font='13px system-ui';ctx.strokeStyle=css('--mf-chart-grid','#d9e0e6');ctx.fillStyle=css('--mf-chart-text','#4f6272');
    for(let i=0;i<4;i++){
      const yy=pad.t+plotH*i/3;ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();
      const val=max-(max-min)*i/3;ctx.fillText(opts.percent?val.toFixed(1)+'%':compact(val),4,yy+4);
    }
    rows.forEach((row,i)=>{
      const v=optionalNumber(row[key]);if(v===null)return;
      const yy=y(v),top=Math.min(zero,yy),height=Math.max(1,Math.abs(zero-yy));
      ctx.fillStyle=opts.signed?(v>=0?css('--mf-chart-bull','#1f7a54'):css('--mf-chart-bear','#a04444')):css('--mf-chart-price','#3a6f99');
      ctx.fillRect(x(i)-barW/2,top,barW,height);
      if(i%Math.max(1,Math.ceil(rows.length/6))===0||i===rows.length-1){
        ctx.fillStyle=css('--mf-chart-text','#4f6272');
        const raw=String(row.label||row.date||row.week_start||'');
        const label=/^\d{4}-\d{2}-\d{2}/.test(raw)?raw.slice(5):raw;
        ctx.fillText(label,Math.max(pad.l,x(i)-18),h-10);
      }
    });
  }

  function tapePriceFlowChart(canvas) {
    let prices=parseData(canvas,'priceSeries').filter(r=>optionalNumber(r.price)!==null&&r.date);
    const flows=parseData(canvas,'flowSeries').filter(r=>optionalNumber(r.cumulative_20d)!==null&&r.date);
    if(!prices.length&&!flows.length)return;
    if(flows.length){
      const first=String(flows[0].date);
      prices=prices.filter(r=>String(r.date)>=first);
    }
    const rect=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
    const w=Math.max(360,rect.width),h=Math.max(220,rect.height||280),pad={l:62,r:78,t:28,b:38};
    canvas.width=w*dpr;canvas.height=h*dpr;
    const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
    const dates=[...prices,...flows].map(r=>new Date(String(r.date)+'T00:00:00').getTime()).filter(Number.isFinite);
    if(!dates.length)return;
    let d0=Math.min(...dates),d1=Math.max(...dates);if(d0===d1)d1=d0+86400000;
    const pv=prices.map(r=>optionalNumber(r.price)).filter(v=>v!==null),fv=flows.map(r=>optionalNumber(r.cumulative_20d)).filter(v=>v!==null);
    let pmin=pv.length?Math.min(...pv):0,pmax=pv.length?Math.max(...pv):1,fmin=fv.length?Math.min(...fv):0,fmax=fv.length?Math.max(...fv):1;
    if(pmin===pmax){pmin-=1;pmax+=1}if(fmin===fmax){fmin-=1;fmax+=1}
    const ps=pmax-pmin,fs=fmax-fmin;pmin-=ps*.08;pmax+=ps*.08;fmin-=fs*.08;fmax+=fs*.08;
    const x=d=>pad.l+(w-pad.l-pad.r)*((new Date(String(d)+'T00:00:00').getTime()-d0)/(d1-d0));
    const yp=v=>pad.t+(h-pad.t-pad.b)*(1-(v-pmin)/(pmax-pmin));
    const yf=v=>pad.t+(h-pad.t-pad.b)*(1-(v-fmin)/(fmax-fmin));
    ctx.font='13px system-ui';ctx.strokeStyle=css('--mf-chart-grid','#d9e0e6');ctx.fillStyle=css('--mf-chart-text','#4f6272');
    for(let i=0;i<4;i++){
      const yy=pad.t+(h-pad.t-pad.b)*i/3;ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();
      ctx.fillText('$'+(pmax-(pmax-pmin)*i/3).toFixed(1),4,yy+4);
      ctx.textAlign='right';ctx.fillText(compact(fmax-(fmax-fmin)*i/3),w-5,yy+4);ctx.textAlign='left';
    }
    const draw=(rows,key,y,color,dash=false)=>{
      ctx.strokeStyle=color;ctx.lineWidth=2.4;ctx.setLineDash(dash?[6,4]:[]);ctx.beginPath();let started=false;
      rows.forEach(r=>{const v=optionalNumber(r[key]);if(v===null)return;const xx=x(r.date),yy=y(v);if(!started){ctx.moveTo(xx,yy);started=true}else ctx.lineTo(xx,yy)});
      ctx.stroke();ctx.setLineDash([]);
    };
    draw(prices,'price',yp,css('--mf-chart-price','#3a6f99'));
    draw(flows,'cumulative_20d',yf,css('--mf-chart-bull','#1f7a54'));
    ctx.fillStyle=css('--mf-chart-price','#3a6f99');ctx.fillText('Price · left',pad.l,pad.t-10);
    ctx.textAlign='right';ctx.fillStyle=css('--mf-chart-bull','#1f7a54');ctx.fillText('Cumulative Large Flow · right',w-pad.r,pad.t-10);ctx.textAlign='left';
  }

  function dualAxisWorkingCapitalChart(canvas, rows) {
    if (!canvas || !rows.length) return;
    const rect=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
    const w=Math.max(300,rect.width),h=Math.max(190,rect.height||230);
    canvas.width=w*dpr;canvas.height=h*dpr;
    const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
    const pad={l:68,r:68,t:28,b:35};
    const leftValues=rows.map(r=>optionalNumber(r.inventory)).filter(v=>v!==null);
    const rightValues=rows.map(r=>optionalNumber(r.receivables)).filter(v=>v!==null);
    if(!leftValues.length&&!rightValues.length)return;
    const bounds=(values)=>{
      if(!values.length)return [0,1];
      let min=Math.min(...values),max=Math.max(...values);
      if(min===max){const bump=Math.max(1,Math.abs(max)*.08);min-=bump;max+=bump}
      const span=max-min||1;return [min-span*.08,max+span*.08];
    };
    const [lmin,lmax]=bounds(leftValues),[rmin,rmax]=bounds(rightValues);
    const x=(i)=>pad.l+(w-pad.l-pad.r)*(rows.length===1?.5:i/(rows.length-1));
    const yl=(v)=>pad.t+(h-pad.t-pad.b)*(1-(v-lmin)/(lmax-lmin));
    const yr=(v)=>pad.t+(h-pad.t-pad.b)*(1-(v-rmin)/(rmax-rmin));
    ctx.font='13px system-ui';ctx.lineWidth=1;
    for(let i=0;i<4;i++){
      const yy=pad.t+(h-pad.t-pad.b)*i/3;
      ctx.strokeStyle=css('--line','#d9e0e6');ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();
      const lv=lmax-(lmax-lmin)*i/3,rv=rmax-(rmax-rmin)*i/3;
      ctx.fillStyle=css('--mf-chart-price','#3a6f99');ctx.textAlign='left';ctx.fillText(compact(lv),5,yy+4);
      ctx.fillStyle=css('--mf-chart-secondary','#6b7f91');ctx.textAlign='right';ctx.fillText(compact(rv),w-5,yy+4);
    }
    ctx.fillStyle=css('--muted','#6d7a86');ctx.textAlign='left';
    rows.forEach((row,i)=>{if(i%Math.max(1,Math.ceil(rows.length/6))===0||i===rows.length-1)ctx.fillText(String(row.label||''),Math.max(pad.l,x(i)-18),h-10)});
    const draw=(key,y,color)=>{
      ctx.strokeStyle=color;ctx.lineWidth=2.3;ctx.beginPath();let started=false;
      rows.forEach((row,i)=>{const v=optionalNumber(row[key]);if(v===null)return;const xx=x(i),yy=y(v);if(!started){ctx.moveTo(xx,yy);started=true}else ctx.lineTo(xx,yy)});
      ctx.stroke();
      rows.forEach((row,i)=>{const v=optionalNumber(row[key]);if(v===null)return;ctx.beginPath();ctx.arc(x(i),y(v),2.4,0,Math.PI*2);ctx.fillStyle=color;ctx.fill()});
    };
    const inventoryColor=css('--mf-chart-price','#3a6f99'),receivablesColor=css('--mf-chart-secondary','#6b7f91');
    draw('inventory',yl,inventoryColor);draw('receivables',yr,receivablesColor);
    ctx.font='13px system-ui';ctx.textAlign='left';ctx.fillStyle=inventoryColor;ctx.fillText('Inventory · left scale',pad.l,pad.t-10);
    ctx.textAlign='right';ctx.fillStyle=receivablesColor;ctx.fillText('Receivables · right scale',w-pad.r,pad.t-10);ctx.textAlign='left';
  }

  function revenueFcfChart(canvas, rows) {
    rows=(rows||[]).filter(row=>optionalNumber(row.revenue)!==null || optionalNumber(row.forecast_revenue)!==null || optionalNumber(row.fcf)!==null);
    if(!canvas||!rows.length)return;
    const rect=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
    const w=Math.max(360,rect.width),h=Math.max(220,rect.height||270),pad={l:62,r:70,t:30,b:42};
    canvas.width=w*dpr;canvas.height=h*dpr;
    const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
    const revVals=rows.map(r=>optionalNumber(r.revenue)??optionalNumber(r.forecast_revenue)).filter(v=>v!==null);
    const fcfVals=rows.map(r=>optionalNumber(r.fcf)).filter(v=>v!==null);
    if(!revVals.length&&!fcfVals.length)return;
    let revMax=Math.max(1,...revVals.map(v=>Math.max(0,v)));
    let fMin=fcfVals.length?Math.min(0,...fcfVals):0,fMax=fcfVals.length?Math.max(1,...fcfVals):1;
    revMax*=1.12;
    if(fMin===fMax){fMin-=1;fMax+=1}
    const fSpan=fMax-fMin;fMin-=fSpan*.08;fMax+=fSpan*.08;
    const plotW=w-pad.l-pad.r,plotH=h-pad.t-pad.b,slot=plotW/rows.length,barW=Math.max(8,Math.min(42,slot*.58));
    const x=i=>pad.l+slot*i+slot/2;
    const yRev=v=>pad.t+plotH*(1-Math.max(0,v)/revMax);
    const yFcf=v=>pad.t+plotH*(1-(v-fMin)/(fMax-fMin));
    ctx.font='13px system-ui';ctx.strokeStyle=css('--mf-chart-grid','#d9e0e6');ctx.fillStyle=css('--mf-chart-text','#4f6272');ctx.lineWidth=1;
    for(let i=0;i<4;i++){
      const yy=pad.t+plotH*i/3;ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();
      ctx.fillText(compact(revMax*(1-i/3)),4,yy+4);
      ctx.textAlign='right';ctx.fillText(compact(fMax-(fMax-fMin)*i/3),w-5,yy+4);ctx.textAlign='left';
    }
    rows.forEach((row,i)=>{
      const hist=optionalNumber(row.revenue),forecast=optionalNumber(row.forecast_revenue),rev=hist??forecast;
      if(rev!==null){
        const yy=yRev(rev),base=pad.t+plotH;
        ctx.globalAlpha=forecast!==null&&hist===null?.45:.88;
        ctx.fillStyle=css('--mf-chart-price','#3a6f99');
        ctx.fillRect(x(i)-barW/2,yy,barW,Math.max(1,base-yy));
        ctx.globalAlpha=1;
      }
      if(i%Math.max(1,Math.ceil(rows.length/7))===0||i===rows.length-1){
        ctx.fillStyle=css('--mf-chart-text','#4f6272');
        ctx.fillText(String(row.label||''),Math.max(pad.l,x(i)-18),h-12);
      }
    });
    let started=false;ctx.strokeStyle=css('--mf-chart-bull','#1f7a54');ctx.lineWidth=2.5;ctx.beginPath();
    rows.forEach((row,i)=>{
      const value=optionalNumber(row.fcf);if(value===null){started=false;return}
      const xx=x(i),yy=yFcf(value);if(!started){ctx.moveTo(xx,yy);started=true}else ctx.lineTo(xx,yy);
    });ctx.stroke();
    rows.forEach((row,i)=>{const value=optionalNumber(row.fcf);if(value===null)return;ctx.beginPath();ctx.arc(x(i),yFcf(value),2.6,0,Math.PI*2);ctx.fillStyle=css('--mf-chart-bull','#1f7a54');ctx.fill()});
    ctx.fillStyle=css('--mf-chart-price','#3a6f99');ctx.fillText('Revenue · columns',pad.l,pad.t-10);
    ctx.textAlign='right';ctx.fillStyle=css('--mf-chart-bull','#1f7a54');ctx.fillText('FCF · line',w-pad.r,pad.t-10);ctx.textAlign='left';
  }

  function optionalNumber(value) {
    if (value === null || value === undefined || value === '') return null;
    const out=Number(value);
    return Number.isFinite(out) ? out : null;
  }
  function valuationChart(canvas) {
    const history=parseData(canvas,'history').filter((row)=>optionalNumber(row.price)!==null);
    const levels=(() => { try { return JSON.parse(canvas.dataset.levels||'{}'); } catch (_) { return {}; } })();
    const bear=optionalNumber(levels.bear),base=optionalNumber(levels.base),bull=optionalNumber(levels.bull),current=optionalNumber(levels.current);
    const scenarioDefs=[
      bear!==null?{key:'bear',label:'Bear',color:css('--mf-chart-bear','#a13b3b'),dash:true}:null,
      base!==null?{key:'base',label:'Base',color:css('--mf-chart-price','#3a6f99'),dash:true}:null,
      bull!==null?{key:'bull',label:'Bull',color:css('--mf-chart-bull','#1f7a54'),dash:true}:null,
    ].filter(Boolean);
    if(!history.length && current===null && !scenarioDefs.length) return;
    const rows=history.length
      ? history.map((row)=>({date:String(row.date).slice(0,7),price:optionalNumber(row.price),bear,base,bull}))
      : [{label:'Current',price:current,bear,base,bull},{label:'Scenario',price:current,bear,base,bull}];
    const defs=[current!==null||history.length?{key:'price',label:'Market',color:css('--mf-chart-market','#7f8a94'),width:2.4}:null,...scenarioDefs].filter(Boolean);
    lineChart(canvas,rows,defs,false);
  }
  function tapePriceShortChart(canvas) {
    const prices=parseData(canvas,'priceSeries').filter(r=>optionalNumber(r.price)!==null && r.date);
    const shorts=parseData(canvas,'shortSeries').filter(r=>optionalNumber(r.short)!==null && r.date);
    if(!prices.length && !shorts.length)return;
    const rect=canvas.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2);
    const w=Math.max(360,rect.width),h=Math.max(220,rect.height||300),pad={l:58,r:72,t:26,b:36};
    canvas.width=w*dpr; canvas.height=h*dpr;
    const ctx=canvas.getContext('2d'); ctx.scale(dpr,dpr);
    const allDates=[...prices,...shorts].map(r=>new Date(String(r.date)+'T00:00:00').getTime()).filter(Number.isFinite);
    if(!allDates.length)return;
    let d0=Math.min(...allDates),d1=Math.max(...allDates); if(d0===d1)d1=d0+86400000;
    const pVals=prices.map(r=>optionalNumber(r.price)).filter(v=>v!==null),sVals=shorts.map(r=>optionalNumber(r.short)).filter(v=>v!==null);
    let pMin=pVals.length?Math.min(...pVals):0,pMax=pVals.length?Math.max(...pVals):1,sMin=sVals.length?Math.min(...sVals):0,sMax=sVals.length?Math.max(...sVals):1;
    if(pMin===pMax){pMin-=Math.max(1,pMin*.05);pMax+=Math.max(1,pMax*.05)}
    if(sMin===sMax){sMin=Math.max(0,sMin*.95);sMax=Math.max(1,sMax*1.05)}
    const pSpan=Math.max(.01,pMax-pMin),sSpan=Math.max(1,sMax-sMin);
    pMin-=pSpan*.08;pMax+=pSpan*.08;sMin=Math.max(0,sMin-sSpan*.08);sMax+=sSpan*.08;
    const x=d=>pad.l+(w-pad.l-pad.r)*((new Date(String(d)+'T00:00:00').getTime()-d0)/(d1-d0));
    const yp=v=>pad.t+(h-pad.t-pad.b)*(1-(v-pMin)/(pMax-pMin));
    const ys=v=>pad.t+(h-pad.t-pad.b)*(1-(v-sMin)/(sMax-sMin));
    ctx.font='13px system-ui';ctx.fillStyle=css('--mf-chart-text','#4f6272');ctx.strokeStyle=css('--mf-chart-grid','#d9e0e6');ctx.lineWidth=1;
    for(let i=0;i<4;i++){
      const yy=pad.t+(h-pad.t-pad.b)*i/3;
      ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();
      ctx.fillText('$'+(pMax-(pMax-pMin)*i/3).toFixed(1),5,yy+4);
      const sv=sMax-(sMax-sMin)*i/3;ctx.textAlign='right';ctx.fillText(compact(sv),w-5,yy+4);ctx.textAlign='left';
    }
    ctx.strokeStyle=css('--mf-chart-price','#3a6f99');ctx.lineWidth=2.5;ctx.beginPath();
    prices.forEach((r,i)=>{const xx=x(r.date),yy=yp(Number(r.price));i?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy)});ctx.stroke();
    if(shorts.length){
      ctx.strokeStyle=css('--mf-chart-bear','#a04444');ctx.lineWidth=2;ctx.setLineDash([6,4]);ctx.beginPath();
      shorts.forEach((r,i)=>{const xx=x(r.date),yy=ys(Number(r.short));i?ctx.lineTo(xx,yy):ctx.moveTo(xx,yy)});ctx.stroke();ctx.setLineDash([]);
      shorts.forEach(r=>{ctx.beginPath();ctx.arc(x(r.date),ys(Number(r.short)),2.8,0,Math.PI*2);ctx.fillStyle=css('--mf-chart-bear','#a04444');ctx.fill()});
    }
    const marks=[d0,d0+(d1-d0)/2,d1];ctx.fillStyle=css('--mf-chart-text','#4f6272');
    marks.forEach((d,i)=>{const label=new Date(d).toLocaleDateString([],{month:'short',year:'2-digit'});const xx=pad.l+(w-pad.l-pad.r)*i/2;ctx.fillText(label,Math.max(pad.l,Math.min(w-pad.r-30,xx-16)),h-10)});
    ctx.fillStyle=css('--mf-chart-price','#3a6f99');ctx.fillText('Price',pad.l,pad.t-10);
    ctx.textAlign='right';ctx.fillStyle=css('--mf-chart-bear','#a04444');ctx.fillText('Short interest',w-pad.r,pad.t-10);ctx.textAlign='left';
  }
  const primary=css('--primary','#3a6f99'), secondary='#7b96ad', accent='#5f8a86';
  function renderCharts(){
    document.querySelectorAll('canvas[data-mf-chart="numbers-scale"]').forEach((node)=>revenueFcfChart(node,parseData(node)));
    document.querySelectorAll('canvas[data-mf-chart="numbers-margin"]').forEach((node)=>lineChart(node,parseData(node),[{key:'op_margin',label:'Operating margin',color:primary},{key:'fcf_margin',label:'FCF margin',color:accent},{key:'forecast_op_margin',label:'Op margin forecast',color:primary,dash:true}],true));
    document.querySelectorAll('canvas[data-mf-chart="working-capital"]').forEach((node)=>dualAxisWorkingCapitalChart(node,parseData(node)));
    document.querySelectorAll('canvas[data-mf-chart="tape-price-flow"]').forEach(tapePriceFlowChart);
    document.querySelectorAll('canvas[data-mf-chart="tape-volume"]').forEach((node)=>barChart(node,parseData(node),'volume'));
    document.querySelectorAll('canvas[data-mf-chart="tape-price-short"]').forEach(tapePriceShortChart);
    document.querySelectorAll('canvas[data-mf-chart="tape-short"]').forEach((node)=>lineChart(node,parseData(node),[{key:'short_pct',label:'Daily short volume %',color:secondary}],true));
    document.querySelectorAll('canvas[data-mf-chart="tape-flow"]').forEach((node)=>barChart(node,parseData(node),'net_large',{signed:true}));
    document.querySelectorAll('canvas[data-mf-chart="tape-cumulative-flow"]').forEach((node)=>lineChart(node,parseData(node),[{key:'cumulative_5d',label:'5D Large Flow',color:primary},{key:'cumulative_20d',label:'20D Large Flow',color:accent}],false));
    document.querySelectorAll('canvas[data-mf-chart="tape-scores"]').forEach((node)=>lineChart(node,parseData(node),[{key:'absorption',label:'Absorption',color:accent},{key:'short_pressure',label:'Short Pressure',color:css('--mf-chart-bear','#a04444')},{key:'net_tape',label:'Net Tape',color:primary}],false));
    document.querySelectorAll('canvas[data-mf-chart="tape-whale"]').forEach((node)=>barChart(node,parseData(node),'net_whale',{signed:true}));
    document.querySelectorAll('canvas[data-mf-chart="tape-ats"]').forEach((node)=>lineChart(node,parseData(node).map(r=>({...r,label:r.week_start||r.date||''})),[{key:'ats_share_pct',label:'ATS share %',color:secondary}],true));
    document.querySelectorAll('canvas[data-mf-chart="valuation"]').forEach(valuationChart);
  }
  renderCharts();

  // Historical-price backfills are heavy background work, but their stored rows
  // should appear in Valuation without forcing a page reload.
  const valuationHistoryCanvas=document.querySelector('canvas[data-mf-chart="valuation"]');
  if(valuationHistoryCanvas && !parseData(valuationHistoryCanvas,'history').length && ticker){
    let priceHistoryPolls=0;
    const pollPriceHistory=async()=>{
      priceHistoryPolls+=1;
      try{
        const response=await fetch('/company/'+encodeURIComponent(ticker)+'/price/history/live',{credentials:'same-origin',headers:{Accept:'application/json'},cache:'no-store'});
        if(!response.ok)throw new Error('history '+response.status);
        const payload=await response.json();
        const state=document.querySelector('[data-price-history-state]');
        const job=document.querySelector('[data-price-history-job]');
        const note=document.querySelector('[data-price-history-note]');
        if(state){
          const span=(payload.first_date&&payload.last_date)?' · '+payload.first_date+' → '+payload.last_date:'';
          const provider=payload.provider?' · '+payload.provider:'';
          state.textContent=String(payload.count||0)+' plotted points'+span+provider;
        }
        if(job && payload.job?.id) job.textContent='Job #'+payload.job.id+' · '+(payload.job.status||'');
        if(Array.isArray(payload.rows)&&payload.rows.length){
          valuationHistoryCanvas.dataset.history=JSON.stringify(payload.rows);
          valuationChart(valuationHistoryCanvas);
          if(note)note.remove();
          return;
        }
        if(payload.job?.status==='FAILED'){
          if(note)note.textContent='Historical price backfill failed: '+(payload.job.error||'provider unavailable')+'. Use Refresh 2Y price history to retry.';
          return;
        }
        if(priceHistoryPolls<30)window.setTimeout(pollPriceHistory,4000);
      }catch(_){
        if(priceHistoryPolls<15)window.setTimeout(pollPriceHistory,5000);
      }
    };
    window.setTimeout(pollPriceHistory,2000);
  }
  let resizeTimer=null;
  window.addEventListener('resize',()=>{window.clearTimeout(resizeTimer);resizeTimer=window.setTimeout(renderCharts,180)});
  window.addEventListener('mf-theme-change',()=>window.setTimeout(renderCharts,30));

  // Central semantic status contract. Components expose meaning; CSS owns color.
  const semanticGroups = {
    positive: ['POSITIVE','GOOD','ATTRACTIVE','FAVORABLE','SUPPORTIVE','MET','PASS','STRENGTH','STRONG','SOUND','CLEAN','BULLISH','LONG','READY','APPROVED','DONE','COMPLETE','COMPLETED','SUCCESS','VALIDATED','PUBLIC','OK'],
    negative: ['NEGATIVE','BAD','EXPENSIVE','DEMANDING','HOSTILE','MISS','FAIL','WEAKNESS','RED FLAG','RED FLAGS','FRAGILE','BLOCKER','CRITICAL','BEARISH','SHORT','FAILED','ERROR','DETERIORATING'],
    caution: ['MIXED','UNCLEAR','PENDING','WATCH','WAIT','UNDER REVIEW','LIMITED','REVIEW','MISSING EVIDENCE','PENDING APPROVAL','UNRESOLVED','INSUFFICIENT EVIDENCE','UNKNOWN','LOW DATA','NEEDS EVIDENCE'],
    info: ['RUNNING','QUEUED','INFO','SYSTEM','VALIDATION','CHECKING','LOCKED'],
    neutral: ['NEUTRAL','FAIR','BALANCED','IN LINE','UNRATED','LATERAL','SIDEWAYS','NO DATA','NOT RUN','NOT STARTED','HOLD'],
    cancelled: ['CANCELLED','SUPERSEDED']
  };
  function semanticStatus(value) {
    const valueText=String(value||'').trim().toUpperCase().replaceAll('_',' ');
    if(!valueText)return'neutral';
    for(const [group,tokens] of Object.entries(semanticGroups)){
      if(tokens.some(token=>valueText===token||valueText.startsWith(token+' ')||valueText.endsWith(' '+token)))return group;
    }
    return 'neutral';
  }
  function applySemanticStatuses(scope=document) {
    scope.querySelectorAll?.('.status-chip,.gate-status,[data-status-value]').forEach((el)=>{
      const value=el.dataset.statusValue||el.textContent||'';
      el.dataset.semantic=semanticStatus(value);
    });
    scope.querySelectorAll?.('.lens-card').forEach((el)=>{
      el.dataset.semantic=semanticStatus(el.querySelector('strong')?.textContent||'');
    });
  }
  applySemanticStatuses();

  // Lightweight Research Control mutations update immediately. These are simple
  // database state changes, not background analytical jobs.
  document.addEventListener('submit', async (event) => {
    const form = event.target.closest?.('.gate-approval-form');
    if (!form) return;
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const prior = button?.textContent || '';
    if (button) { button.disabled = true; button.textContent = 'Saving…'; }
    const formData = new FormData(form);
    const endpoint = form.getAttribute('action');
    if (!endpoint) return;
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        credentials: 'same-origin',
        headers: {Accept: 'application/json', 'X-CSRFToken': csrf},
        body: formData,
        cache: 'no-store'
      });
      const payload = await response.json().catch(()=>({}));
      if (!response.ok || !payload?.ok) throw new Error(payload?.message || ('gate '+response.status));
      const current = document.querySelector('[data-process-readiness]');
      if (!payload?.html || !current) throw new Error('gate response');
      const shell = document.createElement('div');
      shell.innerHTML = payload.html.trim();
      const next = shell.firstElementChild;
      if (!next) throw new Error('gate markup');
      current.replaceWith(next);
      applySemanticStatuses(next);
      if (payload?.research_conclusion) {
        document.querySelectorAll('[data-research-conclusion]').forEach((el)=>{
          el.textContent = payload.research_conclusion;
        });
      }
      dirty = false;
    } catch (error) {
      if (button) { button.disabled = false; button.textContent = prior; }
      const row = form.closest('.gate-row');
      let note = row?.querySelector('.gate-inline-error');
      if (!note && row) {
        note = document.createElement('small');
        note.className = 'gate-inline-error';
        row.appendChild(note);
      }
      if (note) note.textContent = error?.message || 'Unable to update readiness.';
    }
  });

  // CONTROL background-job observer + detached executor fallback.
  // Page requests stay fast: /jobs/pump only starts a separate CLI process and returns.
  const workerChip = document.getElementById('mf-worker-chip');
  if (realRole !== 'CONTROL' || effectiveRole !== 'CONTROL' || !csrf) return;
  const autoRefresh = document.querySelector('meta[name="mf-auto-refresh"]')?.content === '1';
  const pumpLockKey='mf-job-pump-kick-at';
  let busy=false, pumpBusy=false, stopped=false, timer=null, baselineFinished=null, dirty=false, refreshOffered=false;

  document.addEventListener('input',(event)=>{
    const target=event.target;
    if(target && (target.matches('input:not([type="hidden"]):not([type="search"]), textarea, select'))) dirty=true;
  },{capture:true});
  document.addEventListener('submit',()=>{dirty=false},{capture:true});

  function renderJobs(state){
    if(!workerChip)return;
    const queued=Number(state?.queued||0),running=Number(state?.running||0),failed=Number(state?.failed||0);
    const active=Array.isArray(state?.active_jobs)?state.active_jobs:[];
    const lead=active.find((row)=>row.status==='RUNNING')||active[0]||null;
    const target=lead?.target?String(lead.target):'';
    workerChip.title=active.length
      ? active.slice(0,6).map((row)=>'#'+row.id+' '+row.type+' · '+row.target+' · '+row.status).join('\n')
      : 'No active background jobs.';
    if(refreshOffered){workerChip.textContent='Data updated · refresh';workerChip.classList.add('job-updated');return}
    workerChip.classList.remove('job-updated');
    if(running)workerChip.textContent='Jobs · '+(target||'GLOBAL')+' · running · '+queued+' queued';
    else if(queued)workerChip.textContent='Jobs · '+(target||'GLOBAL')+' · queued · '+queued;
    else if(failed)workerChip.textContent='Jobs · idle · '+failed+' failed';
    else workerChip.textContent='Jobs · idle';
  }
  function schedule(ms){window.clearTimeout(timer);if(!stopped)timer=window.setTimeout(tick,ms)}
  async function status(){
    const r=await fetch('/jobs/status',{credentials:'same-origin',headers:{Accept:'application/json'},cache:'no-store'});
    if(r.status===401||r.status===403){stopped=true;return null}
    if(!r.ok)throw new Error('status '+r.status);
    return r.json();
  }
  async function kickExecutor(state){
    if(pumpBusy||Number(state?.due||0)<=0||Number(state?.running||0)>0)return false;
    const now=Date.now();
    let previous=0;
    try{previous=Number(window.localStorage.getItem(pumpLockKey)||0)}catch(_){}
    if(now-previous<12000)return false;
    try{window.localStorage.setItem(pumpLockKey,String(now))}catch(_){}
    pumpBusy=true;
    try{
      const r=await fetch('/jobs/pump',{
        method:'POST',credentials:'same-origin',
        headers:{Accept:'application/json','X-CSRFToken':csrf},
        cache:'no-store'
      });
      if(r.status===401||r.status===403){stopped=true;return false}
      if(!r.ok){
        if(workerChip){workerChip.textContent='Jobs · executor unavailable';workerChip.title='Background executor could not start.'}
        return false;
      }
      const payload=await r.json();
      if(payload?.spawned&&workerChip)workerChip.textContent='Jobs · starting background worker';
      return Boolean(payload?.spawned);
    }catch(_){
      if(workerChip)workerChip.textContent='Jobs · executor reconnecting';
      return false;
    }finally{pumpBusy=false}
  }
  function maybeRefresh(state){
    const finished=state?.last_finished_id??null;
    if(baselineFinished===null){baselineFinished=finished;return false}
    if(finished===null||finished===baselineFinished)return false;
    baselineFinished=finished;
    if(!autoRefresh)return false;
    if(ticker) readQuote();
    refreshOffered=true;
    if(workerChip){
      workerChip.title=dirty
        ? 'Background data finished. Unsaved edits are protected; click when ready to refresh.'
        : 'Background data finished. Click when you want to refresh the full research surface.';
      workerChip.style.cursor='pointer';
      workerChip.onclick=()=>window.location.reload();
    }
    return false;
  }
  async function tick(){
    if(busy||stopped)return;
    busy=true;
    try{
      const state=await status();if(!state)return;
      if(maybeRefresh(state))return;
      renderJobs(state);
      const kicked=await kickExecutor(state);
      const active=Number(state.running||0)+Number(state.queued||0);
      schedule(kicked?1200:(active?2500:8000));
    }catch(_){
      if(workerChip)workerChip.textContent='Jobs · reconnecting';
      schedule(12000);
    }finally{busy=false}
  }
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&!busy&&!stopped)schedule(150)});
  schedule(250);

})();