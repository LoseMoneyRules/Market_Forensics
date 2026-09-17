(() => {
  const $ = (s, r=document) => r.querySelector(s);
  const $$ = (s, r=document) => [...r.querySelectorAll(s)];
  const esc = (v) => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const num = (v) => Number.isFinite(Number(v)) ? Number(v) : null;
  const money = (v) => num(v) === null ? '—' : `$${Number(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`;
  const css = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const match = location.pathname.match(/^\/company\/([^/]+)(?:\/([^/]+))?/);
  const ticker = match ? decodeURIComponent(match[1]).toUpperCase() : null;
  const section = match ? (match[2] || 'overview') : null;
  let businessData = null;
  let expectationsData = null;
  let valuationData = null;
  let reconciling = false;

  async function getJson(url) {
    try {
      const res = await fetch(url, {credentials:'same-origin', headers:{Accept:'application/json'}});
      if (!res.ok) return null;
      return await res.json();
    } catch (_) { return null; }
  }

  function list(items, empty) {
    if (!items?.length) return `<p class="muted">${esc(empty)}</p>`;
    return `<ul>${items.slice(0,6).map(x => `<li>${esc(typeof x === 'string' ? x : `${x.label}: ${x.detail}`)}</li>`).join('')}</ul>`;
  }

  function restoreOverview() {
    if (section !== 'overview') return;

    const strip = $('.research-intelligence-strip');
    const original = window.__mf0171OriginalResearchStrip;
    if (strip && original && (strip.classList.contains('synthesis-016') || strip.innerHTML !== original)) {
      strip.innerHTML = original;
      strip.classList.remove('synthesis-016');
    }

    $('#mf-evidence-path-017')?.remove();

    const form = $('form[action*="/research/overview"]');
    const support = $('.two-col.overview-support-017');
    if (form && support && form.parentElement !== support) {
      support.insertBefore(form, support.firstElementChild);
    }
  }

  async function ensureBusinessEvidence() {
    if (section !== 'business' || !ticker || $('#mf-business-evidence-0171')) return;
    if (!businessData) businessData = await getJson(`/company/${encodeURIComponent(ticker)}/surface/017/overview`);
    const s = businessData?.synthesis;
    const tabs = $('.company-tabs');
    if (!s || !tabs || $('#mf-business-evidence-0171')) return;
    const panel = document.createElement('div');
    panel.id = 'mf-business-evidence-0171';
    panel.className = 'panel business-evidence-path-0171';
    panel.innerHTML = `
      <div class="panel-head"><div><h2>Evidence path · MICRO / MACRO / for / against / invalidation</h2><p class="muted">Always visible here because this is the business evidence map.</p></div></div>
      <div class="evidence-path-body-017">
        <div class="evidence-path-block-017"><h3>MICRO · FOR</h3>${list(s.micro_for,'No positive micro signal above threshold.')}</div>
        <div class="evidence-path-block-017"><h3>MICRO · AGAINST</h3>${list(s.micro_against,'No automatic counter-signal above threshold.')}</div>
        <div class="evidence-path-block-017"><h3>MACRO / INDUSTRY</h3>${list(s.macro,'No stored macro evidence.')}</div>
        <div class="evidence-path-block-017"><h3>INVALIDATION / NEXT</h3><p>${esc(s.invalidation || 'No locked thesis invalidation stored.')}</p>${list(s.next,'Continue monitoring.')}</div>
      </div>`;
    tabs.after(panel);
  }

  function setupCanvas(canvas, height=300) {
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(560, canvas.clientWidth || 720);
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.height = `${height}px`;
    const c = canvas.getContext('2d');
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, width, height);
    c.font = '11px system-ui, -apple-system, sans-serif';
    return {c, width, height};
  }

  function chartColors() {
    return {
      text: css('--mf-chart-text','#4f6272'),
      grid: css('--mf-chart-grid','#d9e0e6'),
      market: css('--mf-chart-market','#7f8a94'),
      base: css('--mf-chart-price','#3a6f99'),
      bear: css('--mf-chart-bear','#a04444'),
      bull: css('--mf-chart-bull','#39745d'),
      zero: css('--mf-zero','#8d99a3'),
    };
  }

  function drawExpectations(canvas, rows) {
    const data = (rows || []).filter(r => num(r.delta_pct) !== null || num(r.delta) !== null).slice(0,18)
      .map(r => ({...r, value:num(r.delta_pct) ?? num(r.delta)}));
    const {c,width:w,height:h} = setupCanvas(canvas, 300);
    const col = chartColors();
    if (!data.length) {
      c.fillStyle = col.text;
      c.fillText('Add Market and Our View values to show expectation gaps.', 20, 40);
      return;
    }
    const pad={l:54,r:20,t:24,b:78};
    const max=Math.max(1,...data.map(r=>Math.abs(r.value)))*1.15;
    const zero=pad.t+(h-pad.t-pad.b)/2;
    const slot=(w-pad.l-pad.r)/data.length;
    c.strokeStyle=col.grid;c.lineWidth=1;
    [pad.t,zero,h-pad.b].forEach(y=>{c.beginPath();c.moveTo(pad.l,y);c.lineTo(w-pad.r,y);c.stroke();});
    c.strokeStyle=col.zero;c.setLineDash([5,4]);c.beginPath();c.moveTo(pad.l,zero);c.lineTo(w-pad.r,zero);c.stroke();c.setLineDash([]);
    data.forEach((r,i)=>{
      const bh=Math.abs(r.value)/max*((h-pad.t-pad.b)/2);
      const x=pad.l+i*slot+slot*.18;
      const y=r.value>=0?zero-bh:zero;
      c.fillStyle=r.value>=0?col.bull:col.bear;
      c.fillRect(x,y,Math.max(4,slot*.64),bh);
      c.fillStyle=col.text;c.textAlign='center';
      c.fillText(`${r.value>=0?'+':''}${r.value.toFixed(1)}${r.delta_pct!=null?'%':''}`,x+slot*.32,r.value>=0?Math.max(12,y-6):Math.min(h-pad.b+14,y+bh+14));
      c.save();c.translate(x+slot*.32,h-9);c.rotate(-.65);c.textAlign='left';c.fillText(String(r.label||'').slice(0,26),0,0);c.restore();
    });
    c.textAlign='left';c.fillStyle=col.text;c.fillText('MARKET = 0',pad.l+4,zero-7);
  }

  async function ensureExpectationsChart() {
    if (section !== 'expectations' || !ticker) return;
    if (!expectationsData) expectationsData = await getJson(`/company/${encodeURIComponent(ticker)}/surface/017/expectations`);
    const tabs = $('.company-tabs');
    if (!tabs || !expectationsData) return;
    $('#mf-expectations-017')?.remove();
    let panel = $('#mf-expectations-0171');
    if (!panel) {
      panel = document.createElement('div');
      panel.id = 'mf-expectations-0171';
      panel.className = 'panel chart-card expectations-chart-017';
      panel.innerHTML = '<div class="panel-head"><div><h2>Our view vs market expectation</h2><p class="muted">Above zero = our assumption is higher; below zero = lower. The chart shows divergence, not an automatic investment conclusion.</p></div></div><canvas></canvas><div class="chart-legend-017"><span class="chart-key-017 bull"><i></i>Positive gap</span><span class="chart-key-017 bear"><i></i>Negative gap</span><span class="chart-key-017 neutral"><i></i>Market = 0</span></div>';
      tabs.after(panel);
    }
    const canvas = $('canvas', panel);
    if (canvas) drawExpectations(canvas, expectationsData.expectations || []);
  }

  function drawValuation(canvas, rows, levels) {
    const data=(rows||[]).filter(r=>num(r.price)!==null).map(r=>({date:String(r.date),price:num(r.price)}));
    const {c,width:w,height:h}=setupCanvas(canvas,300);
    const col=chartColors();
    if(!data.length){c.fillStyle=col.text;c.fillText('Two-year price history is not stored yet.',20,40);return;}
    const all=[...data.map(r=>r.price),...['bear','base','bull'].map(k=>num(levels?.[k])).filter(v=>v!==null)];
    let min=Math.min(...all),max=Math.max(...all),span=max-min||1;min-=span*.08;max+=span*.08;
    const pad={l:58,r:24,t:24,b:42};
    const x=i=>pad.l+i/Math.max(1,data.length-1)*(w-pad.l-pad.r);
    const y=v=>pad.t+(max-v)/(max-min)*(h-pad.t-pad.b);
    c.fillStyle=col.text;c.strokeStyle=col.grid;
    for(let i=0;i<=4;i++){const yy=pad.t+i*(h-pad.t-pad.b)/4,val=max-i*(max-min)/4;c.beginPath();c.moveTo(pad.l,yy);c.lineTo(w-pad.r,yy);c.stroke();c.fillText(`$${val.toFixed(0)}`,6,yy+4);}
    [['bear',col.bear],['base',col.base],['bull',col.bull]].forEach(([key,color])=>{const v=num(levels?.[key]);if(v===null)return;const yy=y(v);c.save();c.strokeStyle=color;c.setLineDash([7,4]);c.lineWidth=1.6;c.beginPath();c.moveTo(pad.l,yy);c.lineTo(w-pad.r,yy);c.stroke();c.restore();c.fillStyle=color;c.textAlign='right';c.fillText(`${key.toUpperCase()} ${money(v)}`,w-pad.r-2,yy-5);});
    c.strokeStyle=col.market;c.lineWidth=2.3;c.setLineDash([]);c.beginPath();data.forEach((r,i)=>i?c.lineTo(x(i),y(r.price)):c.moveTo(x(i),y(r.price)));c.stroke();
    c.fillStyle=col.text;c.textAlign='center';const step=Math.max(1,Math.ceil(data.length/6));data.forEach((r,i)=>{if(i%step&&i!==data.length-1)return;c.fillText(r.date.slice(0,7),x(i),h-14);});c.textAlign='left';
  }

  async function redrawValuation() {
    if (section !== 'valuation' || !ticker) return;
    if (!valuationData) valuationData = await getJson(`/company/${encodeURIComponent(ticker)}/surface/017/valuation`);
    const panel = $('#mf-valuation-017');
    const canvas = panel ? $('canvas',panel) : null;
    if (!canvas || !valuationData) return;
    const keys = $$('.chart-key-017', panel);
    if (keys[0]) keys[0].classList.add('market');
    if (keys[2]) keys[2].classList.add('base');
    drawValuation(canvas, valuationData.price_history || [], valuationData.levels || {});
  }

  function removeFinancialFlowExplainer() {
    $$('.panel').forEach(panel => {
      const t = panel.textContent.replace(/\s+/g,' ').trim().toLowerCase();
      if (t.includes('how to read it') && t.includes('follow the money, then check the bridge')) panel.remove();
    });
    $$('h1,h2,h3,p,.eyebrow').forEach(el => {
      const t = el.textContent.replace(/\s+/g,' ').trim().toLowerCase();
      if (t === 'how to read it' || t === 'follow the money, then check the bridge.' || t === 'follow the money, then check the bridge') el.remove();
    });
  }

  function accountEmailUI() {
    $$('.alert-email-row-017').forEach(row => {
      if (row.dataset.accountEmail0171 === '1') return;
      const input = $('input[type="email"]', row);
      const email = input?.value?.trim() || '';
      const box = document.createElement('div');
      box.className = 'alert-account-email-0171';
      box.dataset.accountEmail0171 = '1';
      box.innerHTML = email ? `Alert email · <strong>${esc(email)}</strong><br><small>Uses the email registered on this account.</small>` : 'Alert email uses the email registered on this account.';
      row.replaceWith(box);
    });
  }

  function installMobileNavigation() {
    const body = document.body;
    const oldMenu = document.getElementById('mf-mobile-menu');
    if (oldMenu && oldMenu.dataset.mf0171 !== '1') {
      const menu = oldMenu.cloneNode(true);
      menu.dataset.mf0171 = '1';
      oldMenu.replaceWith(menu);
      menu.addEventListener('click', (event) => {
        event.preventDefault();event.stopPropagation();
        const open = !body.classList.contains('nav-open');
        body.classList.toggle('nav-open', open);
        body.classList.remove('tools-open');
        menu.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
    }
    const oldTools = document.getElementById('mf-mobile-tools');
    if (oldTools && oldTools.dataset.mf0171 !== '1') {
      const tools = oldTools.cloneNode(true);
      tools.dataset.mf0171 = '1';
      oldTools.replaceWith(tools);
      tools.addEventListener('click', (event) => {
        event.preventDefault();event.stopPropagation();
        const open = !body.classList.contains('tools-open');
        body.classList.toggle('tools-open', open);
        body.classList.remove('nav-open');
        tools.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
    }
    $('#mf-mobile-backdrop')?.addEventListener('click', () => {body.classList.remove('nav-open','tools-open');});
    $$('.navrail a').forEach(a => { if (a.dataset.mfClose0171 !== '1') { a.dataset.mfClose0171='1'; a.addEventListener('click',()=>body.classList.remove('nav-open','tools-open')); } });
  }

  function localizeUtcTimes(root=document.body) {
    if (!root) return;
    const skip = new Set(['SCRIPT','STYLE','TEXTAREA','INPUT','SELECT','OPTION','CODE','PRE']);
    const re = /\b(20\d{2}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::(\d{2}))?(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?\b/g;
    const formatter = new Intl.DateTimeFormat(undefined,{year:'numeric',month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'});
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes=[];while(walker.nextNode())nodes.push(walker.currentNode);
    nodes.forEach(node => {
      const parent=node.parentElement;if(!parent || skip.has(parent.tagName) || parent.closest('.json-preview,.code-block,.error-id'))return;
      const text=node.nodeValue;if(!text || !re.test(text)){re.lastIndex=0;return;}re.lastIndex=0;
      node.nodeValue=text.replace(re,(full,d,t,s,tz)=>{
        const zone = tz ? (tz === 'Z' || tz.includes(':') ? tz : `${tz.slice(0,3)}:${tz.slice(3)}`) : 'Z';
        const parsed = new Date(`${d}T${t}:${s||'00'}${zone}`);
        return Number.isNaN(parsed.getTime()) ? full : formatter.format(parsed);
      });
    });
  }

  function reconcile() {
    if (reconciling) return;
    reconciling = true;
    requestAnimationFrame(async () => {
      try {
        restoreOverview();
        removeFinancialFlowExplainer();
        accountEmailUI();
        installMobileNavigation();
        localizeUtcTimes();
        if (section === 'business') await ensureBusinessEvidence();
        if (section === 'expectations') await ensureExpectationsChart();
        if (section === 'valuation') await redrawValuation();
      } finally { reconciling = false; }
    });
  }

  const observer = new MutationObserver(() => reconcile());
  observer.observe(document.documentElement,{childList:true,subtree:true});
  document.addEventListener('keydown',e=>{if(e.key==='Escape')document.body.classList.remove('nav-open','tools-open');});
  window.addEventListener('resize',()=>{clearTimeout(window.__mf0171Resize);window.__mf0171Resize=setTimeout(()=>{if(section==='expectations')ensureExpectationsChart();if(section==='valuation')redrawValuation();},180);});
  new MutationObserver(()=>{if(section==='expectations')ensureExpectationsChart();if(section==='valuation')redrawValuation();}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  reconcile();
  setTimeout(reconcile,150);
  setTimeout(reconcile,700);
})();
