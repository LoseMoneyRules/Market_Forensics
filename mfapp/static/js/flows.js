(() => {
  'use strict';
  const NS='http://www.w3.org/2000/svg';
  const css=(name,fallback)=>getComputedStyle(document.documentElement).getPropertyValue(name).trim()||fallback;
  const fmt=(value)=>{
    const n=Number(value); if(!Number.isFinite(n)) return '—';
    const sign=n<0?'−':''; const a=Math.abs(n);
    if(a>=1e12)return sign+(a/1e12).toFixed(2)+'T';
    if(a>=1e9)return sign+(a/1e9).toFixed(2)+'B';
    if(a>=1e6)return sign+(a/1e6).toFixed(2)+'M';
    if(a>=1e3)return sign+(a/1e3).toFixed(1)+'K';
    return sign+a.toLocaleString(undefined,{maximumFractionDigits:0});
  };
  const safeData=(root)=>{try{return JSON.parse(root.dataset.flow||'{}')}catch(_){return{}}};
  const deduction=/COGS|EXPENSE|TAX|CAPITAL EXPENDITURE|BUYBACK|DIVIDEND|DISTRIBUTION/i;
  const positive=/PROFIT|INCOME|FREE CASH FLOW|RETAINED/i;

  function make(tag,cls,text){
    const el=document.createElement(tag); if(cls)el.className=cls; if(text!==undefined)el.textContent=text; return el;
  }
  function makeSvg(tag,attrs={}){
    const el=document.createElementNS(NS,tag);
    Object.entries(attrs).forEach(([k,v])=>el.setAttribute(k,String(v)));
    return el;
  }
  function semanticForEdge(edge){
    const label=(edge.target||'')+' '+(edge.label||'');
    if(String(edge.kind||'').toUpperCase()==='WARNING')return'negative';
    if(deduction.test(label))return'negative';
    if(positive.test(label))return'positive';
    return'info';
  }
  function edgeColor(edge){
    const kind=semanticForEdge(edge);
    if(kind==='negative')return css('--semantic-negative','#a04444');
    if(kind==='positive')return css('--semantic-positive','#39745d');
    return css('--semantic-info','#3a6f99');
  }
  function fallbackNodes(edges){
    const names=[...new Set(edges.flatMap(e=>[e.source,e.target]).filter(Boolean))];
    return names.map(label=>{
      const incoming=edges.filter(e=>e.target===label).reduce((s,e)=>s+Math.abs(Number(e.value)||0),0);
      const outgoing=edges.filter(e=>e.source===label).reduce((s,e)=>s+Math.abs(Number(e.value)||0),0);
      return {label,value:Math.max(incoming,outgoing),role:'SUBTOTAL'};
    });
  }
  function graphLayout(edges,nodeRows){
    const names=[...new Set(edges.flatMap(e=>[e.source,e.target]).filter(Boolean))];
    const incoming=new Map(names.map(n=>[n,[]])), outgoing=new Map(names.map(n=>[n,[]]));
    edges.forEach(e=>{incoming.get(e.target)?.push(e);outgoing.get(e.source)?.push(e)});
    const depth=new Map(), roots=names.filter(n=>!(incoming.get(n)||[]).length);
    roots.forEach(n=>depth.set(n,0));
    const queue=[...roots];
    while(queue.length){
      const n=queue.shift(),d=depth.get(n)||0;
      (outgoing.get(n)||[]).forEach(e=>{
        const next=Math.max(depth.get(e.target)||0,d+1);
        if(next!==(depth.get(e.target)||0)){depth.set(e.target,next);queue.push(e.target)}
        else if(!depth.has(e.target)){depth.set(e.target,d+1);queue.push(e.target)}
      });
    }
    names.forEach(n=>{if(!depth.has(n))depth.set(n,0)});
    const maxDepth=Math.max(0,...depth.values());
    const columns=Array.from({length:maxDepth+1},()=>[]);
    names.forEach(n=>columns[depth.get(n)||0].push(n));
    const roleOf=new Map(nodeRows.map(n=>[n.label,String(n.role||'').toUpperCase()]));
    columns.forEach(col=>col.sort((a,b)=>{
      const score=x=>deduction.test(x)?3:positive.test(x)?1:roleOf.get(x)==='ANCHOR'?0:2;
      return score(a)-score(b)||a.localeCompare(b);
    }));
    return {names,incoming,outgoing,columns,maxDepth};
  }
  function wrapText(text,max=23){
    const words=String(text||'').split(/\s+/),lines=[];let line='';
    words.forEach(word=>{
      const candidate=(line+' '+word).trim();
      if(candidate.length>max&&line){lines.push(line);line=word}else line=candidate;
    });
    if(line)lines.push(line);
    return lines.slice(0,2);
  }
  function drawDiagram(root,data,edges,nodeRows){
    const layout=graphLayout(edges,nodeRows);
    if(!layout.names.length)return false;
    const nodeMap=new Map(nodeRows.map(n=>[n.label,n]));
    const nodeW=150,nodeH=52,top=14,side=10,rowGap=12;
    const visible=Math.floor(root.getBoundingClientRect().width||0);
    const targetWidth=Math.max(720,visible>0?visible-24:720);
    const colGap=layout.maxDepth>0?Math.max(170,(targetWidth-side*2-nodeW)/layout.maxDepth):0;
    const maxRows=Math.max(1,...layout.columns.map(c=>c.length));
    const width=Math.ceil(side*2+nodeW+(layout.maxDepth*colGap));
    const height=Math.max(150,top*2+maxRows*nodeH+(maxRows-1)*rowGap);
    const svg=makeSvg('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':(data.flow_type||'Financial')+' flow '+(data.period||'')});
    svg.classList.add('flow-svg');
    const labelFont='11',valueFont='12',labelLine='13';
    const pos=new Map();
    layout.columns.forEach((col,ci)=>{
      const total=col.length*nodeH+Math.max(0,col.length-1)*rowGap;
      const start=Math.max(top,(height-total)/2);
      col.forEach((name,ri)=>pos.set(name,{x:side+ci*colGap,y:start+ri*(nodeH+rowGap)}));
    });
    const maxValue=Math.max(1,...edges.map(e=>Math.abs(Number(e.value)||0)));
    edges.forEach(edge=>{
      const a=pos.get(edge.source),b=pos.get(edge.target);if(!a||!b)return;
      const x1=a.x+nodeW,y1=a.y+nodeH/2,x2=b.x,y2=b.y+nodeH/2;
      const curve=Math.max(34,(x2-x1)*.44);
      const path=makeSvg('path',{d:`M ${x1} ${y1} C ${x1+curve} ${y1}, ${x2-curve} ${y2}, ${x2} ${y2}`,fill:'none',stroke:edgeColor(edge),'stroke-opacity':'.42','stroke-width':Math.max(3,Math.min(34,Math.abs(Number(edge.value)||0)/maxValue*34)),'stroke-linecap':'round'});
      const title=makeSvg('title');title.textContent=(edge.label||edge.source+' → '+edge.target)+': '+fmt(edge.signed_value??edge.value);path.appendChild(title);svg.appendChild(path);
    });
    layout.names.forEach(name=>{
      const p=pos.get(name),meta=nodeMap.get(name)||{},role=String(meta.role||'').toUpperCase();
      const group=makeSvg('g',{'data-flow-node':name}), rect=makeSvg('rect',{x:p.x,y:p.y,width:nodeW,height:nodeH,rx:10,fill:css('--flow-node-bg',css('--white','#fff')),stroke:deduction.test(name)?css('--semantic-negative-line','#d8b9b9'):positive.test(name)?css('--semantic-positive-line','#b8d6c6'):css('--line','#d9e0e6'),'stroke-width':role==='ANCHOR'?2:1});
      group.appendChild(rect);
      const label=makeSvg('text',{x:p.x+12,y:p.y+22,fill:css('--navy','#0b1f33'),'font-size':labelFont,'font-weight':650});
      wrapText(name).forEach((line,i)=>{const t=makeSvg('tspan',{x:p.x+12,dy:i?labelLine:'0'});t.textContent=line;label.appendChild(t)});group.appendChild(label);
      const incoming=(layout.incoming.get(name)||[]).reduce((s,e)=>s+Math.abs(Number(e.value)||0),0),outgoing=(layout.outgoing.get(name)||[]).reduce((s,e)=>s+Math.abs(Number(e.value)||0),0);
      const value=Number.isFinite(Number(meta.value))?Number(meta.value):Math.max(incoming,outgoing);
      const val=makeSvg('text',{x:p.x+12,y:p.y+nodeH-12,fill:css('--muted','#6d7a86'),'font-size':valueFont,'font-weight':650});val.textContent=fmt(value);group.appendChild(val);
      svg.appendChild(group);
    });
    const scroll=make('div','flow-diagram-scroll');scroll.appendChild(svg);root.appendChild(scroll);
    const mobile=make('div','flow-mobile-ledger');
    edges.forEach(edge=>{
      const row=make('div','flow-mobile-edge');row.dataset.semantic=semanticForEdge(edge);
      const copy=make('div');copy.append(make('strong',null,edge.source+' → '+edge.target),make('span',null,edge.label||''));
      row.append(copy,make('b',null,fmt(edge.signed_value??edge.value)));mobile.appendChild(row);
    });
    root.appendChild(mobile);
    return true;
  }
  function render(root,suppliedData=null){
    const data=suppliedData||safeData(root); root.innerHTML='';
    const rawEdges=Array.isArray(data.edges)?data.edges:[];
    const edges=rawEdges.filter(e=>Number.isFinite(Number(e.value))&&Number(e.value)>=0);
    const exceptions=Array.isArray(data.signed_exceptions)?data.signed_exceptions.filter(Boolean):[];
    const nodeRows=Array.isArray(data.nodes)&&data.nodes.length?data.nodes:fallbackNodes(edges);

    const head=make('div','flow-title');
    const title=make('div');title.append(make('strong',null,String(data.flow_type||'Financial Flow').replaceAll('_',' ')),make('small','muted',String(data.period||'')));
    const scale=make('span','flow-scale-note','Ribbon width = magnitude');head.append(title,scale);root.appendChild(head);

    if(!edges.length){
      const empty=make('div','empty-state');empty.append(make('h2',null,'No reconciled positive flow stored'),make('p',null,'Negative and exceptional facts remain signed below; they are never converted into fake positive ribbons.'));root.appendChild(empty);
    }else drawDiagram(root,data,edges,nodeRows);

    if(exceptions.length){
      const box=make('section','flow-exceptions');box.appendChild(make('strong',null,'Signed exceptions / losses'));
      exceptions.forEach(edge=>{const row=make('div','flow-exception');row.dataset.semantic='negative';row.append(make('span',null,edge.label||edge.source+' → '+edge.target),make('strong',null,fmt(edge.signed_value??edge.value)));box.appendChild(row)});root.appendChild(box);
    }
    if(Array.isArray(data.reconciliations)&&data.reconciliations.length){
      const box=make('section','flow-reconciliation');box.appendChild(make('strong',null,'Accounting bridge checks'));
      data.reconciliations.forEach(rec=>{const row=make('div','flow-reconciliation-row');row.dataset.semantic=rec.ok?'positive':'negative';row.append(make('span',null,rec.label),make('span',null,'Δ '+fmt(rec.delta)),make('strong',null,rec.ok?'RECONCILED':'REVIEW'));box.appendChild(row)});root.appendChild(box);
    }
    if(Array.isArray(data.warnings)&&data.warnings.length){
      const notice=make('div','notice amber');notice.append(make('strong',null,'Flow review. '),document.createTextNode(data.warnings.join(' ')));root.appendChild(notice);
    }
    if(Array.isArray(data.derived)&&data.derived.length){
      root.appendChild(make('p','flow-derived','Derived bridges: '+data.derived.join('; ')));
    }
  }

  document.querySelectorAll('.flow-tab').forEach(button=>button.addEventListener('click',()=>{
    document.querySelectorAll('.flow-tab').forEach(x=>{x.classList.remove('active');x.setAttribute('aria-selected','false')});
    document.querySelectorAll('.flow-panel').forEach(x=>x.classList.remove('active'));
    button.classList.add('active');button.setAttribute('aria-selected','true');
    const panel=document.getElementById(button.dataset.flowTarget);panel?.classList.add('active');
    const root=panel?.querySelector('.flow-canvas');if(root)window.requestAnimationFrame(()=>render(root));
  }));
  const renderAll=()=>document.querySelectorAll('.flow-canvas').forEach(root=>render(root));
  window.MFRenderFlow=render;
  renderAll();
  let resizeTimer=null;
  window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(renderAll,180)});
  window.addEventListener('mf-theme-change',()=>setTimeout(renderAll,20));
})();