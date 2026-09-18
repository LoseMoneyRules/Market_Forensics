(() => {
  'use strict';

  const root = document.documentElement;
  const body = document.body;
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
  const realRole = document.querySelector('meta[name="mf-real-role"]')?.content || '';
  const effectiveRole = document.querySelector('meta[name="mf-effective-role"]')?.content || '';

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

  // Mobile research-step selector.
  document.querySelectorAll('.company-tabs').forEach((tabs, index) => {
    const active = tabs.querySelector('a.active')?.textContent?.trim() || 'Research steps';
    const id = tabs.id || ('mf-research-tabs-' + index);
    tabs.id = id;
    const toggle = document.createElement('button');
    toggle.type = 'button';
    toggle.className = 'company-tabs-toggle';
    toggle.setAttribute('aria-controls', id);
    toggle.setAttribute('aria-expanded', 'false');
    toggle.innerHTML = '<span>Research step</span><strong></strong>';
    toggle.querySelector('strong').textContent = active;
    tabs.parentNode?.insertBefore(toggle, tabs);
    const close = () => { tabs.classList.remove('mobile-open'); toggle.setAttribute('aria-expanded', 'false'); };
    toggle.addEventListener('click', () => {
      const open = !tabs.classList.contains('mobile-open');
      tabs.classList.toggle('mobile-open', open);
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
    tabs.addEventListener('click', (event) => { if (event.target.closest('a')) close(); });
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

  // Live market reference.
  const ticker = document.querySelector('.ticker-badge')?.textContent?.trim();
  const priceNode = document.querySelector('[data-live-price]');
  const priceMeta = document.querySelector('[data-live-price-meta]');
  const marketBox = priceNode?.closest('.company-market');
  function fmtTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? iso : d.toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
  }
  async function readQuote() {
    if (!ticker) return;
    try {
      const response = await fetch('/company/' + encodeURIComponent(ticker) + '/price/live', {credentials:'same-origin',headers:{Accept:'application/json'}});
      if (!response.ok) return;
      const q = await response.json();
      if (priceNode && Number.isFinite(Number(q.price))) priceNode.textContent = '$' + Number(q.price).toFixed(2);
      if (priceMeta) {
        priceMeta.textContent = '';
        const dot = document.createElement('i');
        dot.className = 'live-dot';
        priceMeta.append(dot, document.createTextNode((q.provider || 'quote') + ' · ' + fmtTime(q.as_of)));
      }
    } catch (_) {}
  }
  async function requestQuote() {
    if (!ticker || !csrf || !location.pathname.includes('/company/')) return;
    marketBox?.classList.add('refreshing');
    try {
      await fetch('/company/' + encodeURIComponent(ticker) + '/price/refresh', {method:'POST',credentials:'same-origin',headers:{Accept:'application/json','X-CSRFToken':csrf}});
      window.setTimeout(readQuote, 2500);
      window.setTimeout(readQuote, 9000);
    } catch (_) {}
    finally { window.setTimeout(() => marketBox?.classList.remove('refreshing'), 3000); }
  }
  if (ticker && location.pathname.includes('/company/')) {
    window.setTimeout(requestQuote, 1200);
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
    ctx.font='11px system-ui'; ctx.fillStyle=css('--muted','#6d7a86'); ctx.strokeStyle=css('--line','#d9e0e6'); ctx.lineWidth=1;
    for(let i=0;i<4;i++){const yy=pad.t+(h-pad.t-pad.b)*i/3;ctx.beginPath();ctx.moveTo(pad.l,yy);ctx.lineTo(w-pad.r,yy);ctx.stroke();const val=max-(max-min)*i/3;ctx.fillText(percent?val.toFixed(1)+'%':compact(val),5,yy+4)}
    rows.forEach((row,i)=>{if(i%Math.max(1,Math.ceil(rows.length/6))===0||i===rows.length-1)ctx.fillText(String(row.label||row.date||''),Math.max(pad.l,x(i)-18),h-10)});
    defs.forEach((d)=>{ctx.strokeStyle=d.color;ctx.lineWidth=d.width||2.2;ctx.setLineDash(d.dash?[6,5]:[]);ctx.beginPath();let started=false;rows.forEach((row,i)=>{const v=Number(row[d.key]);if(!Number.isFinite(v))return;const xx=x(i),yy=y(v);if(!started){ctx.moveTo(xx,yy);started=true}else ctx.lineTo(xx,yy)});ctx.stroke();ctx.setLineDash([]);});
  }
  function valuationChart(canvas) {
    const history=parseData(canvas,'history').filter((row)=>Number.isFinite(Number(row.price)));
    const levels=(() => { try { return JSON.parse(canvas.dataset.levels||'{}'); } catch (_) { return {}; } })();
    if(!history.length) return;
    const rows=history.map((row)=>({date:String(row.date).slice(0,7),price:Number(row.price),bear:Number(levels.bear),base:Number(levels.base),bull:Number(levels.bull)}));
    lineChart(canvas,rows,[
      {key:'price',label:'Market',color:'#7f8a94',width:2.4},
      {key:'bear',label:'Bear',color:'#a13b3b',dash:true},
      {key:'base',label:'Base',color:'#3a6f99',dash:true},
      {key:'bull',label:'Bull',color:'#1f7a54',dash:true},
    ],false);
  }
  const primary=css('--primary','#3a6f99'), secondary='#7b96ad', accent='#5f8a86';
  function renderCharts(){
    document.querySelectorAll('canvas[data-mf-chart="numbers-scale"]').forEach((node)=>lineChart(node,parseData(node),[{key:'revenue',label:'Revenue',color:primary},{key:'fcf',label:'FCF',color:accent},{key:'forecast_revenue',label:'Revenue forecast',color:primary,dash:true}],false));
    document.querySelectorAll('canvas[data-mf-chart="numbers-margin"]').forEach((node)=>lineChart(node,parseData(node),[{key:'op_margin',label:'Operating margin',color:primary},{key:'fcf_margin',label:'FCF margin',color:accent},{key:'forecast_op_margin',label:'Op margin forecast',color:primary,dash:true}],true));
    document.querySelectorAll('canvas[data-mf-chart="working-capital"]').forEach((node)=>lineChart(node,parseData(node),[{key:'inventory',label:'Inventory',color:primary},{key:'receivables',label:'Receivables',color:secondary}],false));
    document.querySelectorAll('canvas[data-mf-chart="tape-price"]').forEach((node)=>lineChart(node,parseData(node),[{key:'price',label:'Price',color:primary}],false));
    document.querySelectorAll('canvas[data-mf-chart="tape-short"]').forEach((node)=>lineChart(node,parseData(node),[{key:'short_pct',label:'Daily short volume %',color:secondary}],true));
    document.querySelectorAll('canvas[data-mf-chart="valuation"]').forEach(valuationChart);
  }
  renderCharts();
  let resizeTimer=null;
  window.addEventListener('resize',()=>{window.clearTimeout(resizeTimer);resizeTimer=window.setTimeout(renderCharts,180)});
  window.addEventListener('mf-theme-change',()=>window.setTimeout(renderCharts,30));

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
    if(refreshOffered){workerChip.textContent='Data updated · refresh';workerChip.classList.add('job-updated');return}
    workerChip.classList.remove('job-updated');
    if(running)workerChip.textContent='Jobs · running · '+queued+' queued';
    else if(queued)workerChip.textContent='Jobs · starting · '+queued+' queued';
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
    if(!dirty){
      window.location.reload();
      return true;
    }
    refreshOffered=true;
    if(workerChip){
      workerChip.title='Background data finished. Click to refresh when you are ready.';
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