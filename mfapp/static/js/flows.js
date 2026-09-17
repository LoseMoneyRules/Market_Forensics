(() => {
  const NS='http://www.w3.org/2000/svg';
  const css=(name,fallback)=>getComputedStyle(document.documentElement).getPropertyValue(name).trim()||fallback;
  const fmt=(value)=>{const n=Number(value);if(!Number.isFinite(n))return'—';const sign=n<0?'−':'';const a=Math.abs(n);if(a>=1e12)return`${sign}${(a/1e12).toFixed(2)}T`;if(a>=1e9)return`${sign}${(a/1e9).toFixed(2)}B`;if(a>=1e6)return`${sign}${(a/1e6).toFixed(2)}M`;return`${sign}${a.toLocaleString(undefined,{maximumFractionDigits:0})}`};

  document.querySelectorAll('.flow-tab').forEach(button=>button.addEventListener('click',()=>{
    document.querySelectorAll('.flow-tab').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.flow-panel').forEach(x=>x.classList.remove('active'));
    button.classList.add('active');document.getElementById(button.dataset.flowTarget)?.classList.add('active');
  }));

  function fallbackNodes(edges){
    const names=[...new Set(edges.flatMap(e=>[e.source,e.target]))];
    return names.map((label,index)=>{
      const incoming=edges.filter(e=>e.target===label).reduce((s,e)=>s+(Number(e.value)||0),0);
      const outgoing=edges.filter(e=>e.source===label).reduce((s,e)=>s+(Number(e.value)||0),0);
      return {label,value:Math.max(incoming,outgoing),role:index===0?'ANCHOR':'SUBTOTAL',order:null,derived:true};
    });
  }

  function render(root, suppliedData=null){
    let data=suppliedData||{};
    if(!suppliedData){try{data=JSON.parse(root.dataset.flow||'{}')}catch(_){data={}}}
    root.innerHTML='';
    const edges=Array.isArray(data.edges)?data.edges.filter(e=>Number(e.value)>=0):[];
    const exceptions=Array.isArray(data.signed_exceptions)?data.signed_exceptions.filter(Boolean):[];
    const nodeRows=Array.isArray(data.nodes)&&data.nodes.length?data.nodes:fallbackNodes(edges);
    const nodeMap=new Map(nodeRows.map(n=>[n.label,n]));
    const primary=css('--primary','#3a6f99'), line=css('--line','#d9e0e6'), white=css('--white','#fff'), navy=css('--navy','#0b1f33'), muted=css('--muted','#6d7a86'), watch=css('--watch','#9a6a12');

    const title=document.createElement('div');title.className='flow-title';
    title.innerHTML=`<div><strong>${String(data.flow_type||'Financial Flow').replaceAll('_',' ')}</strong><div class="muted">${data.period||''} · engine ${data.calculation_version||'legacy'}</div></div>`;
    root.appendChild(title);

    if(Array.isArray(data.statement_chain)&&data.statement_chain.length){
      const chain=document.createElement('div');chain.className='flow-chain-016';
      data.statement_chain.forEach((name,i)=>{
        const row=nodeMap.get(name)||{};const item=document.createElement('div');item.className=`flow-chain-node ${String(row.role||'').toLowerCase()}`;
        item.innerHTML=`<span>${name}</span><strong>${fmt(row.value)}</strong>`;chain.appendChild(item);
        if(i<data.statement_chain.length-1){const arrow=document.createElement('span');arrow.className='flow-chain-arrow';arrow.textContent='→';chain.appendChild(arrow)}
      });root.appendChild(chain);
    }

    if(!edges.length){
      const empty=document.createElement('div');empty.className='empty-state';empty.innerHTML='<h2>No reconciled positive flow stored</h2><p>Missing or negative facts remain visible as signed exceptions instead of being converted into fake positive widths.</p>';root.appendChild(empty);
    }else{
      const edgeNames=[...new Set(edges.flatMap(e=>[e.source,e.target]))];
      edgeNames.forEach(name=>{if(!nodeMap.has(name))nodeMap.set(name,{label:name,value:null,role:'OTHER',order:null,derived:true})});
      const nodes=[...nodeMap.keys()].filter(name=>edgeNames.includes(name));
      const incoming=new Map(nodes.map(n=>[n,[]])),outgoing=new Map(nodes.map(n=>[n,[]]));
      edges.forEach(e=>{if(!outgoing.has(e.source))outgoing.set(e.source,[]);if(!incoming.has(e.target))incoming.set(e.target,[]);outgoing.get(e.source).push(e);incoming.get(e.target).push(e)});

      const depth=new Map();
      nodeRows.forEach(n=>{if(Number.isFinite(Number(n.order)))depth.set(n.label,Number(n.order))});
      const roots=nodes.filter(n=>!(incoming.get(n)||[]).length),queue=roots.map(n=>[n,0]);
      while(queue.length){const[n,d]=queue.shift();if(!depth.has(n))depth.set(n,d);(outgoing.get(n)||[]).forEach(e=>{if(!depth.has(e.target))queue.push([e.target,(depth.get(n)??d)+1])})}
      nodes.forEach(n=>{if(!depth.has(n))depth.set(n,0)});
      const maxDepth=Math.max(...depth.values(),1),columns=Array.from({length:maxDepth+1},()=>[]);
      nodes.forEach(n=>columns[Math.min(maxDepth,Math.max(0,depth.get(n)||0))].push(n));
      columns.forEach(col=>col.sort((a,b)=>{
        const rank=x=>({ANCHOR:0,SUBTOTAL:1,RESULT:1,CONTRIBUTION:2,DEDUCTION:3,LOSS:4}[String(nodeMap.get(x)?.role||'OTHER').toUpperCase()]??5);
        return rank(a)-rank(b);
      }));

      const width=Math.max(860,root.clientWidth-36),maxRows=Math.max(...columns.map(c=>c.length),1),height=Math.max(400,maxRows*104+95),svg=document.createElementNS(NS,'svg');
      svg.setAttribute('viewBox',`0 0 ${width} ${height}`);svg.setAttribute('width','100%');svg.setAttribute('height',String(height));svg.setAttribute('role','img');svg.setAttribute('aria-label',`${data.flow_type||'Financial'} flow ${data.period||''}`);
      const pos=new Map(),nodeW=174,nodeH=66,side=22;
      columns.forEach((col,ci)=>{const x=side+(width-side*2-nodeW)*(maxDepth?ci/maxDepth:0),gap=height/(col.length+1);col.forEach((name,ri)=>pos.set(name,{x,y:gap*(ri+1)-nodeH/2}))});
      const maxValue=Math.max(1,...edges.map(e=>Number(e.value)||0));

      edges.forEach(e=>{const a=pos.get(e.source),b=pos.get(e.target);if(!a||!b)return;const x1=a.x+nodeW,y1=a.y+nodeH/2,x2=b.x,y2=b.y+nodeH/2,c1=x1+(x2-x1)*.44,c2=x1+(x2-x1)*.56,path=document.createElementNS(NS,'path');
        path.setAttribute('d',`M ${x1} ${y1} C ${c1} ${y1}, ${c2} ${y2}, ${x2} ${y2}`);path.setAttribute('fill','none');path.setAttribute('stroke',e.kind==='WARNING'?watch:primary);path.setAttribute('stroke-opacity',e.kind==='BRIDGE'?'.34':'.52');path.setAttribute('stroke-width',String(Math.max(2,Math.min(34,(Number(e.value)||0)/maxValue*34))));path.setAttribute('stroke-linecap','round');const t=document.createElementNS(NS,'title');t.textContent=`${e.label||`${e.source} → ${e.target}`}: ${fmt(e.signed_value??e.value)}`;path.appendChild(t);svg.appendChild(path)
      });

      nodes.forEach(name=>{const p=pos.get(name),meta=nodeMap.get(name)||{},group=document.createElementNS(NS,'g'),rect=document.createElementNS(NS,'rect');
        rect.setAttribute('x',p.x);rect.setAttribute('y',p.y);rect.setAttribute('width',nodeW);rect.setAttribute('height',nodeH);rect.setAttribute('rx','8');rect.setAttribute('fill',white);rect.setAttribute('stroke',String(meta.role||'').toUpperCase()==='ANCHOR'?primary:line);rect.setAttribute('stroke-width',String(meta.role||'').toUpperCase()==='ANCHOR'?'2':'1');group.appendChild(rect);
        const label=document.createElementNS(NS,'text');label.setAttribute('x',p.x+10);label.setAttribute('y',p.y+25);label.setAttribute('font-size','11.5');label.setAttribute('font-weight',String(meta.role||'').toUpperCase()==='ANCHOR'?'700':'600');label.setAttribute('fill',navy);label.textContent=name.length>26?`${name.slice(0,25)}…`:name;group.appendChild(label);
        const incomingSum=(incoming.get(name)||[]).reduce((s,e)=>s+(Number(e.value)||0),0),outgoingSum=(outgoing.get(name)||[]).reduce((s,e)=>s+(Number(e.value)||0),0);
        const value=Number.isFinite(Number(meta.value))?Number(meta.value):Math.max(incomingSum,outgoingSum);const val=document.createElementNS(NS,'text');val.setAttribute('x',p.x+10);val.setAttribute('y',p.y+49);val.setAttribute('font-size','13');val.setAttribute('fill',muted);val.textContent=fmt(value);group.appendChild(val);svg.appendChild(group)
      });root.appendChild(svg);
    }

    if(exceptions.length){const box=document.createElement('div');box.className='flow-exceptions';box.innerHTML='<strong>Signed / exceptional items</strong>';exceptions.forEach(edge=>{const row=document.createElement('div');row.className='flow-exception';row.innerHTML=`<span>${edge.label||`${edge.source} → ${edge.target}`}</span><strong>${fmt(edge.signed_value??edge.value)}</strong>`;box.appendChild(row)});root.appendChild(box)}
    if(Array.isArray(data.reconciliations)&&data.reconciliations.length){const box=document.createElement('div');box.className='flow-reconciliation';box.innerHTML='<strong>Accounting bridge checks</strong>';data.reconciliations.forEach(r=>{const row=document.createElement('div');row.className='flow-reconciliation-row';row.innerHTML=`<span>${r.label}</span><span>Δ ${fmt(r.delta)}</span><strong class="${r.ok?'ok':'fail'}">${r.ok?'RECONCILED':'REVIEW'}</strong>`;box.appendChild(row)});root.appendChild(box)}
    if(Array.isArray(data.warnings)&&data.warnings.length){const notice=document.createElement('div');notice.className='notice amber';notice.innerHTML=`<strong>Flow review.</strong> ${data.warnings.join(' ')}`;root.appendChild(notice)}
    if(Array.isArray(data.derived)&&data.derived.length){const note=document.createElement('p');note.className='muted';note.textContent=`Derived bridges: ${data.derived.join('; ')}`;root.appendChild(note)}
  }
  window.MFRenderFlow=render;
  document.querySelectorAll('.flow-canvas').forEach(root=>render(root));
})();