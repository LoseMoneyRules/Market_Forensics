(() => {
  const $ = (s, r=document) => r.querySelector(s);
  const $$ = (s, r=document) => [...r.querySelectorAll(s)];
  const esc = (v) => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const num = (v) => Number.isFinite(Number(v)) ? Number(v) : null;
  const money = (v) => num(v) === null ? '—' : `$${Number(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`;
  const css = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const csrf = () => $('meta[name="csrf-token"]')?.content || '';
  const pathMatch = location.pathname.match(/^\/company\/([^/]+)(?:\/([^/]+))?/);
  const ticker = pathMatch ? decodeURIComponent(pathMatch[1]).toUpperCase() : null;
  const section = pathMatch ? (pathMatch[2] || 'overview') : null;
  const chartRenders = [];

  function cleanupLegacy() {
    ['.business-status-016','.expectations-chart-016','.valuation-chart-016','.tape-combined-016','.monitor-legend-016','.synthesis-details-016'].forEach(sel => $$(sel).forEach(el => el.remove()));
    $$('.status-chip').forEach(chip => {
      const t = chip.textContent.trim();
      if (/^0\.1\.\d+/i.test(t) || /^engine\s+0\.1\.\d+/i.test(t) || /^Evidence-driven$/i.test(t)) chip.remove();
    });
    $$('.publish-readiness-016').forEach(el => {
      el.classList.remove('publish-readiness-016');
      el.classList.add('publish-readiness-017');
    });
  }

  const legacyObserver = new MutationObserver(cleanupLegacy);
  legacyObserver.observe(document.documentElement, {childList:true, subtree:true});
  cleanupLegacy();

  async function json(url, options={}) {
    try {
      const res = await fetch(url, {credentials:'same-origin', headers:{Accept:'application/json', ...(options.headers||{})}, ...options});
      let body = null;
      try { body = await res.json(); } catch (_) {}
      if (!res.ok) throw new Error(body?.error || `Request failed (${res.status})`);
      return body;
    } catch (err) {
      console.error(err);
      return {ok:false, error:err.message};
    }
  }

  async function surface(name) {
    if (!ticker) return null;
    return json(`/company/${encodeURIComponent(ticker)}/surface/017/${encodeURIComponent(name)}${location.search || ''}`);
  }

  function compact(v) {
    const n = Number(v); if (!Number.isFinite(n)) return '—';
    const a = Math.abs(n), sign = n < 0 ? '−' : '';
    if (a >= 1e12) return `${sign}${(a/1e12).toFixed(1)}T`;
    if (a >= 1e9) return `${sign}${(a/1e9).toFixed(1)}B`;
    if (a >= 1e6) return `${sign}${(a/1e6).toFixed(1)}M`;
    if (a >= 1e3) return `${sign}${(a/1e3).toFixed(1)}K`;
    return `${n.toFixed(a < 10 ? 1 : 0)}`;
  }

  function canvasSetup(canvas, height=300) {
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
      price: css('--mf-chart-price','#3a6f99'),
      bear: css('--mf-chart-bear','#a04444'),
      bull: css('--mf-chart-bull','#39745d'),
      zero: css('--mf-zero','#8d99a3'),
    };
  }

  function drawEmpty(canvas, message) {
    const {c} = canvasSetup(canvas, 280); const col=chartColors();
    c.fillStyle=col.text; c.fillText(message, 20, 40);
  }

  function drawExpectations(canvas, rows) {
    const data = (rows||[]).filter(r => num(r.delta_pct) !== null || num(r.delta) !== null).slice(0,18)
      .map(r => ({...r, value:num(r.delta_pct) ?? num(r.delta)}));
    if (!data.length) return drawEmpty(canvas,'Add Market and Our View values to show expectation gaps.');
    const {c,width:w,height:h}=canvasSetup(canvas,300), col=chartColors(), pad={l:54,r:20,t:24,b:78};
    const max=Math.max(1,...data.map(r=>Math.abs(r.value)))*1.15, zero=pad.t+(h-pad.t-pad.b)/2, slot=(w-pad.l-pad.r)/data.length;
    c.strokeStyle=col.grid; c.lineWidth=1;
    [pad.t,zero,h-pad.b].forEach(y=>{c.beginPath();c.moveTo(pad.l,y);c.lineTo(w-pad.r,y);c.stroke();});
    c.strokeStyle=col.zero;c.setLineDash([5,4]);c.beginPath();c.moveTo(pad.l,zero);c.lineTo(w-pad.r,zero);c.stroke();c.setLineDash([]);
    data.forEach((r,i)=>{
      const bh=Math.abs(r.value)/max*((h-pad.t-pad.b)/2), x=pad.l+i*slot+slot*.18, y=r.value>=0?zero-bh:zero;
      c.fillStyle=r.value>=0?col.bull:col.bear; c.fillRect(x,y,Math.max(4,slot*.64),bh);
      c.fillStyle=col.text;c.textAlign='center';c.fillText(`${r.value>=0?'+':''}${r.value.toFixed(1)}${r.delta_pct!=null?'%':''}`,x+slot*.32,r.value>=0?Math.max(12,y-6):Math.min(h-pad.b+14,y+bh+14));
      c.save();c.translate(x+slot*.32,h-9);c.rotate(-.65);c.textAlign='left';c.fillText(String(r.label||'').slice(0,26),0,0);c.restore();
    });
    c.textAlign='left';c.fillStyle=col.text;c.fillText('MARKET = 0',pad.l+4,zero-7);
  }

  function drawValuation(canvas, rows, levels) {
    const data=(rows||[]).filter(r=>num(r.price)!==null).map(r=>({date:String(r.date),price:num(r.price)}));
    if(!data.length) return drawEmpty(canvas,'Two-year price history is not stored yet.');
    const col=chartColors(), all=[...data.map(r=>r.price),...['bear','base','bull'].map(k=>num(levels?.[k])).filter(v=>v!==null)];
    let min=Math.min(...all),max=Math.max(...all),span=max-min||1;min-=span*.08;max+=span*.08;
    const {c,width:w,height:h}=canvasSetup(canvas,300),pad={l:58,r:24,t:24,b:42};
    const x=i=>pad.l+i/Math.max(1,data.length-1)*(w-pad.l-pad.r),y=v=>pad.t+(max-v)/(max-min)*(h-pad.t-pad.b);
    c.fillStyle=col.text;c.strokeStyle=col.grid;
    for(let i=0;i<=4;i++){const yy=pad.t+i*(h-pad.t-pad.b)/4,val=max-i*(max-min)/4;c.beginPath();c.moveTo(pad.l,yy);c.lineTo(w-pad.r,yy);c.stroke();c.fillText(`$${val.toFixed(0)}`,6,yy+4);}
    [['bear',col.bear],['base',col.price],['bull',col.bull]].forEach(([key,color])=>{const v=num(levels?.[key]);if(v===null)return;const yy=y(v);c.save();c.strokeStyle=color;c.setLineDash([7,4]);c.lineWidth=1.6;c.beginPath();c.moveTo(pad.l,yy);c.lineTo(w-pad.r,yy);c.stroke();c.restore();c.fillStyle=color;c.textAlign='right';c.fillText(`${key.toUpperCase()} ${money(v)}`,w-pad.r-2,yy-5);});
    c.strokeStyle=col.price;c.lineWidth=2.3;c.setLineDash([]);c.beginPath();data.forEach((r,i)=>i?c.lineTo(x(i),y(r.price)):c.moveTo(x(i),y(r.price)));c.stroke();
    c.fillStyle=col.text;c.textAlign='center';const step=Math.max(1,Math.ceil(data.length/6));data.forEach((r,i)=>{if(i%step&&i!==data.length-1)return;c.fillText(r.date.slice(0,7),x(i),h-14);});c.textAlign='left';
  }

  function dateMs(raw){const x=Date.parse(`${String(raw).slice(0,10)}T00:00:00Z`);return Number.isFinite(x)?x:null;}

  function drawTapeMacro(canvas, priceRows, shortRows) {
    const prices=(priceRows||[]).map(r=>({t:dateMs(r.date),v:num(r.price),date:r.date})).filter(r=>r.t!==null&&r.v!==null);
    const shorts=(shortRows||[]).map(r=>({t:dateMs(r.date),v:num(r.short_interest),date:r.date})).filter(r=>r.t!==null&&r.v!==null);
    if(!prices.length || !shorts.length) return drawEmpty(canvas,'Historical price plus reported Short Interest are required for this relationship view.');
    const col=chartColors(), allT=[...prices,...shorts].map(r=>r.t),t0=Math.min(...allT),t1=Math.max(...allT),pmin=Math.min(...prices.map(r=>r.v)),pmax=Math.max(...prices.map(r=>r.v)),smin=Math.min(...shorts.map(r=>r.v)),smax=Math.max(...shorts.map(r=>r.v));
    const {c,width:w,height:h}=canvasSetup(canvas,310),pad={l:60,r:72,t:24,b:42};
    const x=t=>pad.l+(t-t0)/Math.max(1,t1-t0)*(w-pad.l-pad.r), yp=v=>pad.t+(pmax-v)/Math.max(.0001,pmax-pmin)*(h-pad.t-pad.b), ys=v=>pad.t+(smax-v)/Math.max(1,smax-smin)*(h-pad.t-pad.b);
    c.strokeStyle=col.grid;c.fillStyle=col.text;
    for(let i=0;i<=4;i++){const yy=pad.t+i*(h-pad.t-pad.b)/4;c.beginPath();c.moveTo(pad.l,yy);c.lineTo(w-pad.r,yy);c.stroke();c.fillStyle=col.price;c.textAlign='left';c.fillText(`$${(pmax-i*(pmax-pmin)/4).toFixed(0)}`,6,yy+4);c.fillStyle=col.bear;c.textAlign='right';c.fillText(compact(smax-i*(smax-smin)/4),w-6,yy+4);}
    c.strokeStyle=col.price;c.lineWidth=2.2;c.beginPath();prices.forEach((r,i)=>i?c.lineTo(x(r.t),yp(r.v)):c.moveTo(x(r.t),yp(r.v)));c.stroke();
    c.strokeStyle=col.bear;c.lineWidth=2;c.beginPath();shorts.forEach((r,i)=>i?c.lineTo(x(r.t),ys(r.v)):c.moveTo(x(r.t),ys(r.v)));c.stroke();c.fillStyle=col.bear;shorts.forEach(r=>{c.beginPath();c.arc(x(r.t),ys(r.v),3,0,Math.PI*2);c.fill();});
    c.fillStyle=col.text;c.textAlign='center';for(let i=0;i<=5;i++){const t=t0+i*(t1-t0)/5;c.fillText(new Date(t).toISOString().slice(0,7),x(t),h-14);}c.textAlign='left';
  }

  function movingAverage(rows, n=20){return rows.map((r,i)=>{const slice=rows.slice(Math.max(0,i-n+1),i+1).map(x=>x.v);return {...r,avg:slice.reduce((a,b)=>a+b,0)/slice.length};});}

  function drawDailyShort(canvas, rows) {
    let data=(rows||[]).map(r=>({date:String(r.date),t:dateMs(r.date),v:num(r.short_pct)})).filter(r=>r.t!==null&&r.v!==null);
    if(!data.length) return drawEmpty(canvas,'Refresh FINRA to populate daily short-volume activity.');
    data=movingAverage(data,20);const col=chartColors(),vals=data.flatMap(r=>[r.v,r.avg]),min=Math.max(0,Math.min(...vals)-5),max=Math.min(100,Math.max(...vals)+5),t0=data[0].t,t1=data[data.length-1].t;
    const {c,width:w,height:h}=canvasSetup(canvas,290),pad={l:56,r:22,t:24,b:42},x=t=>pad.l+(t-t0)/Math.max(1,t1-t0)*(w-pad.l-pad.r),y=v=>pad.t+(max-v)/Math.max(1,max-min)*(h-pad.t-pad.b);
    c.strokeStyle=col.grid;c.fillStyle=col.text;
    for(let i=0;i<=4;i++){const yy=pad.t+i*(h-pad.t-pad.b)/4,val=max-i*(max-min)/4;c.beginPath();c.moveTo(pad.l,yy);c.lineTo(w-pad.r,yy);c.stroke();c.fillText(`${val.toFixed(0)}%`,7,yy+4);}
    c.strokeStyle=col.bear;c.lineWidth=1.6;c.beginPath();data.forEach((r,i)=>i?c.lineTo(x(r.t),y(r.v)):c.moveTo(x(r.t),y(r.v)));c.stroke();
    c.strokeStyle=col.price;c.lineWidth=2.2;c.beginPath();data.forEach((r,i)=>i?c.lineTo(x(r.t),y(r.avg)):c.moveTo(x(r.t),y(r.avg)));c.stroke();
    c.fillStyle=col.text;c.textAlign='center';for(let i=0;i<=5;i++){const t=t0+i*(t1-t0)/5;c.fillText(new Date(t).toISOString().slice(5,10),x(t),h-14);}c.textAlign='left';
  }

  function list(items, empty) {
    if(!items?.length) return `<p class="muted">${esc(empty)}</p>`;
    return `<ul>${items.slice(0,6).map(x=>`<li>${esc(typeof x==='string'?x:`${x.label}: ${x.detail}`)}</li>`).join('')}</ul>`;
  }

  function movePublicationToReadiness(publishReady) {
    const form=$('.company-actions form[action*="snapshot"]') || $('.publish-readiness-017 form[action*="snapshot"]');
    const process=$$('.panel h2').find(h=>h.textContent.trim()==='Process readiness')?.closest('.panel');
    if(!form || !process) return;
    let wrap=$('.publish-readiness-017',process);
    if(!wrap){wrap=document.createElement('div');wrap.className='publish-readiness-017';wrap.innerHTML='<div class="publish-copy">Publish becomes available when every current evidence gate is approved.</div>';process.appendChild(wrap);}
    if(!wrap.contains(form)) wrap.appendChild(form);
    wrap.classList.toggle('ready',!!publishReady);wrap.classList.toggle('pending',!publishReady);
    form.style.display='';const btn=$('button',form);if(btn)btn.disabled=!publishReady;
    $('.action-hint',form)?.remove();
  }

  function renderOverview(data) {
    const s=data?.synthesis;if(!s)return;
    $('.synthesis-details-016')?.remove();
    const tabs=$('.company-tabs');if(!tabs)return;
    $('#mf-evidence-path-017')?.remove();
    const details=document.createElement('details');details.id='mf-evidence-path-017';details.className='evidence-path-017';details.innerHTML=`
      <summary>Evidence path · MICRO / MACRO / for / against / invalidation</summary>
      <div class="evidence-path-body-017">
        <div class="evidence-path-block-017"><h3>MICRO · FOR</h3>${list(s.micro_for,'No positive micro signal above threshold.')}</div>
        <div class="evidence-path-block-017"><h3>MICRO · AGAINST</h3>${list(s.micro_against,'No automatic counter-signal above threshold.')}</div>
        <div class="evidence-path-block-017"><h3>MACRO / INDUSTRY</h3>${list(s.macro,'No stored macro evidence.')}</div>
        <div class="evidence-path-block-017"><h3>INVALIDATION / NEXT</h3><p>${esc(s.invalidation||'No locked thesis invalidation stored.')}</p>${list(s.next,'Continue monitoring.')}</div>
      </div>`;
    tabs.after(details);
    const researchForm=$('form[action*="/research/overview"]');
    if(researchForm){
      const oldParent=researchForm.closest('.two-col');details.after(researchForm);researchForm.classList.add('research-state-form-017');
      if(oldParent){oldParent.classList.add('overview-support-017');if(!oldParent.children.length)oldParent.remove();}
    }
    movePublicationToReadiness(data.publish_ready);
  }

  function renderBusiness(data) {
    $$('.business-status-016').forEach(x=>x.remove());
    const form=$('form[action*="/research/business"]');const grid=form?.closest('.two-col');if(!grid)return;
    const other=[...grid.children].find(x=>x!==form);if(!other)return;
    other.className='panel business-context-017';other.innerHTML=`<div class="panel-head"><div><h2>Strategic divergences & macro context</h2><p class="muted">Business-model evidence only. Process approvals live in Overview readiness.</p></div></div><div class="business-evidence-grid-017"><div><h3>Strategic divergences</h3>${list(data?.strategic_divergences,'No material strategic divergence stored.')}</div><div><h3>Macro / industry evidence</h3>${list(data?.macro,'No material macro or industry evidence stored.')}</div></div>`;
  }

  function renderExpectations(data) {
    $$('.expectations-chart-016').forEach(x=>x.remove());
    $('#mf-expectations-017')?.remove();const tabs=$('.company-tabs');if(!tabs)return;
    const panel=document.createElement('div');panel.id='mf-expectations-017';panel.className='panel chart-card expectations-chart-017';panel.innerHTML='<div class="panel-head"><div><h2>Our view vs market expectation</h2><p class="muted">Above zero = our assumption is higher; below zero = lower. The chart shows divergence, not an automatic investment conclusion.</p></div></div><canvas></canvas><div class="chart-legend-017"><span class="chart-key-017 bull"><i></i>Positive gap</span><span class="chart-key-017 bear"><i></i>Negative gap</span><span class="chart-key-017 neutral"><i></i>Market = 0</span></div>';
    tabs.after(panel);const canvas=$('canvas',panel);const fn=()=>drawExpectations(canvas,data?.expectations||[]);chartRenders.push(fn);fn();
  }

  function renderValuation(data) {
    $$('.valuation-chart-016').forEach(x=>x.remove());$('#mf-valuation-017')?.remove();
    const anchor=$('.kpi-grid')||$('.company-tabs');if(!anchor)return;
    const panel=document.createElement('div');panel.id='mf-valuation-017';panel.className='panel chart-card valuation-chart-017';panel.innerHTML='<div class="panel-head"><div><h2>2 year price context</h2><p class="muted">Historical market price with today’s Bear / Base / Bull levels. Scenario lines are current values, not backfilled historical fair values.</p></div></div><canvas></canvas><div class="chart-legend-017"><span class="chart-key-017"><i></i>Price</span><span class="chart-key-017 bear"><i></i>Bear</span><span class="chart-key-017"><i></i>Base</span><span class="chart-key-017 bull"><i></i>Bull</span></div>';
    anchor.after(panel);const canvas=$('canvas',panel);const fn=()=>drawValuation(canvas,data?.price_history||[],data?.levels||{});chartRenders.push(fn);fn();
  }

  function renderTape(data) {
    $$('.tape-combined-016').forEach(x=>x.remove());$('#mf-tape-017')?.remove();
    const oldGrid=$('.chart-grid');const anchor=oldGrid||$('.kpi-grid');if(!anchor)return;
    const wrap=document.createElement('div');wrap.id='mf-tape-017';wrap.className='tape-stack-017';wrap.innerHTML=`
      <div class="panel chart-card tape-chart-017"><div class="panel-head"><div><h2>Price vs reported Short Interest · ${esc(data?.months||12)}M</h2><p class="muted">Blue = price, left axis. Red = FINRA reported Short Interest, right axis. The 6M / 12M selector changes the actual historical window.</p></div></div><canvas data-chart="macro"></canvas><div class="chart-legend-017"><span class="chart-key-017"><i></i>Price</span><span class="chart-key-017 bear"><i></i>Short Interest</span></div></div>
      <div class="panel chart-card tape-chart-017"><div class="panel-head"><div><h2>Daily short activity</h2><p class="muted">FINRA daily short-volume share. Red = daily value; blue = 20-day average. This is separate from reported Short Interest.</p></div></div><canvas data-chart="daily"></canvas><div class="chart-legend-017"><span class="chart-key-017 bear"><i></i>Daily short-volume %</span><span class="chart-key-017"><i></i>20-day average</span></div></div>`;
    if(oldGrid) oldGrid.replaceWith(wrap); else anchor.after(wrap);
    const macro=$('canvas[data-chart="macro"]',wrap),daily=$('canvas[data-chart="daily"]',wrap);
    const fn1=()=>drawTapeMacro(macro,data?.price_history||[],data?.short_interest||[]),fn2=()=>drawDailyShort(daily,data?.daily_short||[]);chartRenders.push(fn1,fn2);fn1();fn2();
  }

  function ruleEditor(rule) {
    if(rule.locked) return '<span class="locked-rule-017">Locked · immutable</span>';
    return `<details class="rule-editor-017"><summary>Edit</summary><div class="rule-edit-grid-017">
      <label>Name<input data-field="name" value="${esc(rule.name)}"></label><label>Metric<input data-field="metric" value="${esc(rule.metric)}"></label>
      <label>Operator<select data-field="operator">${['<','<=','>','>=','==','!=','NOTE'].map(v=>`<option ${rule.operator===v?'selected':''}>${esc(v)}</option>`).join('')}</select></label>
      <label>Threshold<input data-field="threshold" value="${rule.threshold??''}"></label><label>Text trigger<input data-field="threshold_text" value="${esc(rule.threshold_text||'')}"></label><label>Unit<input data-field="unit" value="${esc(rule.unit||'')}"></label>
      <label>Severity<select data-field="severity">${['INFO','WATCH','FAIL','CRITICAL'].map(v=>`<option ${rule.severity===v?'selected':''}>${v}</option>`).join('')}</select></label>
    </div><div class="button-row"><button type="button" class="button" data-rule-save="${rule.id}">Save changes</button><button type="button" class="button danger-017" data-rule-remove="${rule.id}">Remove rule</button></div></details>`;
  }

  function renderMonitoring(data) {
    $$('.monitor-legend-016').forEach(x=>x.remove());$('#mf-alert-control-017')?.remove();
    const m=data?.monitoring;if(!m)return;const tabs=$('.company-tabs');if(!tabs)return;
    const panel=document.createElement('div');panel.id='mf-alert-control-017';panel.className='panel alert-delivery-017';
    const systemRows=(m.system_alerts||[]).map(a=>`<div class="alert-switch-017"><div><span class="alert-name">${esc(a.label)}</span><small>Built-in engine alert</small></div><label class="switch-label-017"><input type="checkbox" data-catalog-system="${esc(a.key)}" ${a.member_available?'checked':''}> Members can choose</label><label class="switch-label-017"><input type="checkbox" data-sub-system="${esc(a.key)}" ${a.subscribed?'checked':''}> Notify me</label></div>`).join('');
    const ruleRows=(m.rules||[]).map(r=>`<div class="alert-rule-manage-017" data-rule-row="${r.id}"><div class="alert-switch-017"><div><span class="alert-name">${esc(r.name)}</span><small>${esc([r.metric,r.operator,r.threshold??r.threshold_text,r.unit].filter(x=>x!==null&&x!=='').join(' '))} · ${esc(r.severity)}</small></div><label class="switch-label-017"><input type="checkbox" data-catalog-rule="${r.id}" ${r.member_available?'checked':''}> Members can choose</label><label class="switch-label-017"><input type="checkbox" data-sub-rule="${r.id}" ${r.subscribed?'checked':''}> Notify me</label></div>${ruleEditor(r)}</div>`).join('');
    panel.innerHTML=`<div class="panel-head"><div><h2>Alerts & notifications</h2><p class="muted">CONTROL defines the alert catalog. Members can only subscribe to alerts you explicitly make available.</p></div><button type="button" class="button primary" id="mf-save-alerts-017">Save alert settings</button></div>
      <div class="alert-delivery-grid-017"><div class="alert-email-box-017"><h3>Delivery</h3><div class="alert-email-row-017"><label>Notification email<input id="mf-alert-email-017" type="email" value="${esc(m.email||'')}"></label><button class="button" type="button" id="mf-save-email-017">Save email</button></div><div class="channel-row-017"><label class="checkbox"><input id="mf-alert-inapp-017" type="checkbox" ${m.subscription?.in_app_enabled?'checked':''}> In-app notifications</label><label class="checkbox"><input id="mf-alert-mail-017" type="checkbox" ${m.subscription?.email_enabled?'checked':''}> Email delivery</label></div><p>${m.smtp_ready?'Email server is configured.':'Email server is not configured yet; in-app alerts still work.'}</p><button class="button" type="button" id="mf-evaluate-017">Evaluate now</button></div><div class="alert-switches-017"><h3>Built-in alerts</h3>${systemRows||'<p class="muted">No built-in alerts.</p>'}</div></div>
      <div class="alert-rule-list-017"><h3>CONTROL rules</h3>${ruleRows||'<p class="muted">No custom rules yet. Use Add alert rule below.</p>'}</div>`;
    tabs.after(panel);

    const approved=$$('.panel h2').find(h=>h.textContent.trim()==='Approved/active rules')?.closest('.panel');if(approved)approved.style.display='none';
    const addForm=$('form[action*="/monitoring"]');if(addForm){const h=$('h2',addForm);if(h)h.textContent='Add alert rule';}

    $('#mf-save-email-017',panel)?.addEventListener('click',async e=>{const btn=e.currentTarget,res=await json('/alerts/017/email',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify({email:$('#mf-alert-email-017',panel)?.value||''})});btn.textContent=res?.ok?'Email saved':(res?.error||'Save failed');});
    $('#mf-save-alerts-017',panel)?.addEventListener('click',async e=>{
      const btn=e.currentTarget;
      const rule_ids=$$('input[data-catalog-rule]:checked',panel).map(x=>Number(x.dataset.catalogRule));
      const system_alerts=$$('input[data-catalog-system]:checked',panel).map(x=>x.dataset.catalogSystem);
      const subRules=$$('input[data-sub-rule]:checked',panel).map(x=>Number(x.dataset.subRule));
      const subSystems=$$('input[data-sub-system]:checked',panel).map(x=>x.dataset.subSystem);
      const catalog=await json(`/company/${encodeURIComponent(ticker)}/alerts/017/catalog`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify({rule_ids,system_alerts})});
      if(!catalog?.ok){btn.textContent=catalog?.error||'Catalog save failed';return;}
      const sub=await json(`/alerts/017/subscription/${m.coverage_id}`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify({rule_ids:subRules,system_alerts:subSystems,email_enabled:$('#mf-alert-mail-017',panel)?.checked,in_app_enabled:$('#mf-alert-inapp-017',panel)?.checked})});
      btn.textContent=sub?.ok?'Saved':'Save failed';setTimeout(()=>btn.textContent='Save alert settings',1600);
    });
    $('#mf-evaluate-017',panel)?.addEventListener('click',async e=>{const btn=e.currentTarget;btn.disabled=true;const res=await json(`/company/${encodeURIComponent(ticker)}/monitoring/evaluate-017`,{method:'POST',headers:{'X-CSRFToken':csrf()}});btn.disabled=false;btn.textContent=res?.evaluated?`Evaluated · ${res.created_alerts||0} new alert(s)`:'Evaluation failed';});
    $$('[data-rule-save]',panel).forEach(btn=>btn.addEventListener('click',async()=>{const id=btn.dataset.ruleSave,row=$(`[data-rule-row="${id}"]`,panel),body={};$$('[data-field]',row).forEach(el=>body[el.dataset.field]=el.value);const res=await json(`/company/${encodeURIComponent(ticker)}/alerts/017/rule/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify(body)});btn.textContent=res?.ok?'Saved':(res?.error||'Save failed');}));
    $$('[data-rule-remove]',panel).forEach(btn=>btn.addEventListener('click',async()=>{if(!confirm('Remove this alert rule? Historical audit records will be retained.'))return;const id=btn.dataset.ruleRemove,res=await json(`/company/${encodeURIComponent(ticker)}/alerts/017/rule/${id}`,{method:'DELETE',headers:{'X-CSRFToken':csrf()}});if(res?.ok)$(`[data-rule-row="${id}"]`,panel)?.remove();else btn.textContent=res?.error||'Remove failed';}));
  }

  async function renderMemberAlerts() {
    const host=$('[data-mf-alert-coverage]');if(!host)return;const coverage=Number(host.dataset.mfAlertCoverage);if(!coverage)return;
    const data=await json(`/alerts/017/subscription/${coverage}`);if(!data?.ok)return;
    const items=[...(data.system_alerts||[]).map(x=>({kind:'system',id:x.key,label:x.label,selected:x.selected,detail:'Market Forensics system alert'})),...(data.rules||[]).map(x=>({kind:'rule',id:x.id,label:x.name,selected:x.selected,detail:`${x.trigger} · ${x.severity}`}))];
    host.className='panel member-alerts-017';host.innerHTML=`<div class="panel-head"><div><h2>Research alerts</h2><p class="muted">Choose which CONTROL-defined alerts you want for this published research.</p></div><button class="button primary" type="button" id="mf-member-alert-save">Save</button></div><div class="alert-email-row-017"><label>Notification email<input id="mf-member-alert-email" type="email" value="${esc(data.email||'')}"></label><div class="channel-row-017"><label class="checkbox"><input id="mf-member-inapp" type="checkbox" ${data.subscription?.in_app_enabled?'checked':''}> In-app</label><label class="checkbox"><input id="mf-member-email-enabled" type="checkbox" ${data.subscription?.email_enabled?'checked':''}> Email</label></div></div><div class="member-alert-list-017">${items.length?items.map(x=>`<label class="member-alert-item-017"><input type="checkbox" data-member-${x.kind}="${esc(x.id)}" ${x.selected?'checked':''}><span>${esc(x.label)}<small>${esc(x.detail)}</small></span></label>`).join(''):'<p class="muted">CONTROL has not made any alerts available for this research yet.</p>'}</div><p class="muted">${data.smtp_ready?'Email delivery is available.':'Email delivery is not configured on the server yet; in-app selections are still saved.'}</p>`;
    $('#mf-member-alert-save',host)?.addEventListener('click',async e=>{const btn=e.currentTarget,payload={email:$('#mf-member-alert-email',host)?.value||'',rule_ids:$$('input[data-member-rule]:checked',host).map(x=>Number(x.dataset.memberRule)),system_alerts:$$('input[data-member-system]:checked',host).map(x=>x.dataset.memberSystem),in_app_enabled:$('#mf-member-inapp',host)?.checked,email_enabled:$('#mf-member-email-enabled',host)?.checked};const res=await json(`/alerts/017/subscription/${coverage}`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf()},body:JSON.stringify(payload)});btn.textContent=res?.ok?'Saved':(res?.error||'Save failed');setTimeout(()=>btn.textContent='Save',1600);});
  }

  function removeDecorativeText() {
    $$('.action-hint').forEach(el=>{if(/one publication|role-specific|evidence engine/i.test(el.textContent))el.remove();});
    $$('.panel-head .status-chip').forEach(el=>{if(/evidence-driven|0\.1\./i.test(el.textContent))el.remove();});
  }

  async function initCompany() {
    if(!ticker)return;const data=await surface(section);if(!data || data.ok===false)return;
    cleanupLegacy();removeDecorativeText();
    if(section==='overview')renderOverview(data);
    else if(section==='business')renderBusiness(data);
    else if(section==='expectations')renderExpectations(data);
    else if(section==='valuation')renderValuation(data);
    else if(section==='tape')renderTape(data);
    else if(section==='monitoring')renderMonitoring(data);
    cleanupLegacy();removeDecorativeText();
  }

  new MutationObserver(()=>{chartRenders.forEach(fn=>fn());}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
  window.addEventListener('resize',()=>{clearTimeout(window.__mf017resize);window.__mf017resize=setTimeout(()=>chartRenders.forEach(fn=>fn()),120);});
  renderMemberAlerts();
  initCompany();
})();
