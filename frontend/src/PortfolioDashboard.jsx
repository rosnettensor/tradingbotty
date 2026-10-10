import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { usePoll, ago } from './useBot.js';
import { BOTS, RANGES, finite, money, signed, seriesFor, domain, segments, nearest, viewport, relativePoints } from './portfolioChart.js';
import './portfolio.css';

const date = (ts, time=false) => ts ? new Date(ts*1000).toLocaleString('de-CH',time?{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}:{day:'2-digit',month:'short',year:'2-digit'}) : '—';
const tone = n => n>0?'up':n<0?'down':'';
const labels = {cash:'Cash',brain:'Daily',fast:'Fast',volatility:'Volatility',own:'Eigene Assets'};
const colors = {cash:'var(--cyan)',own:'var(--dim)',...Object.fromEntries(BOTS.map(b=>[b.id,b.color]))};

export default function PortfolioDashboard() {
  const [range,setRange]=useState('1d'), [mode,setMode]=useState('value'), [view,setView]=useState('line');
  const [refs,setRefs]=useState({brain:true,fast:true,volatility:true,cash:false,hold:false});
  const [events,setEvents]=useState(true), [full,setFull]=useState(false), [zoom,setZoom]=useState([0,100]);
  const [data,reload,poll]=usePoll(`account/history?period=${range}`,30000);
  const dialog=useRef(null), fullButton=useRef(null), wasFull=useRef(false);
  const ready=data?.range===range, d=ready?data:null;
  const stale=!ready||!!poll.error||d?.stale||!!d?.status?.error;
  const chooseRange=r=>{setRange(r);setZoom([0,100]);};
  const chooseMode=m=>{setMode(m);setZoom([0,100]);};
  useEffect(()=>{
    const el=dialog.current;
    if(full){el.showModal();wasFull.current=true;const old=document.body.style.overflow;document.body.style.overflow='hidden';return()=>{el.close();document.body.style.overflow=old;};}
    if(wasFull.current)fullButton.current?.focus();
  },[full]);
  const toggleFull=()=>{
    if(full){if(document.fullscreenElement===dialog.current)document.exitFullscreen?.().catch(()=>{});setFull(false);}
    else setFull(true);
  };
  // The modal fills the viewport even where the optional native Fullscreen API is unavailable.
  const nativeFull=()=>dialog.current?.requestFullscreen?.().catch(()=>{});
  const model=useMemo(()=>seriesFor(d,mode,refs),[d,mode,refs]);
  const visible=useMemo(()=>viewport(model.bars,zoom),[model,zoom]);
  const summary=d?.summary||{}, cur=d?.currency||data?.currency||'CHF';
  const content=<section className={`portfolio-observatory ${full?'expanded':''}`} aria-label="Portfolio-Dashboard">
    <header className="po-header"><div><span className="po-eyebrow"><i className={stale?'':'fresh'}/> PORTFOLIO OBSERVATORY <span>01 / ACCOUNT</span></span><h2>Mein Konto<span className="po-currency">{cur}</span></h2></div>
      <div className="po-actions"><span className={`po-feed ${stale?'warn':''}`}>{poll.error?'VERBINDUNG PRÜFEN':stale?'MESSUNG AUSSTEHEND':'KONTO VERBUNDEN'}<small>{d?.last?`Messpunkt ${ago(d.last)}`:'Noch keine Kontomessung'}</small></span><button ref={full?null:fullButton} onClick={toggleFull} className="po-icon-button" aria-label={full?'Vollbild schließen':'Portfolio im Vollbild öffnen'} title={full?'Schließen · Esc':'Vollbild'}>{full?'✕':<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden><path d="M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6" fill="none" stroke="currentColor" strokeWidth="1.6"/></svg>}</button></div>
    </header>
    <div className="po-headline"><div><span className="po-label">Zuletzt gemessener Kontowert</span><strong key={range}>{money(summary.total)}<small>{cur}</small></strong></div><div className={`po-period-change ${tone(mode==='result'?summary.net_change:summary.change)}`}><b>{signed(mode==='result'?summary.net_change:summary.change)} <small>{cur}</small></b><span>{mode==='value'?`${signed(summary.change_pct)}%`:''}<i>{mode==='result'?'nach erfassten Geldflüssen¹':'Kontowertänderung¹'}{mode==='value'&&finite(summary.flows)&&summary.flows!==0?` · ${signed(summary.flows)} Geldfluss`:''}</i></span></div><div className="po-range" role="group" aria-label="Zeitraum">{RANGES.map(([key,label])=><button key={key} aria-pressed={range===key} onClick={()=>chooseRange(key)}>{label}</button>)}</div></div>
    <div className="po-toolbar"><div className="po-segment" role="group" aria-label="Messgröße"><button aria-pressed={mode==='value'} onClick={()=>chooseMode('value')}>Kontowert</button><button aria-pressed={mode==='result'} onClick={()=>chooseMode('result')}>Ergebnis & Bots</button></div><div className="po-chart-type" role="group" aria-label="Chartdarstellung"><button aria-pressed={view==='line'} onClick={()=>setView('line')}><span aria-hidden>⌁</span> Linie</button><button aria-pressed={view==='candles'} onClick={()=>setView('candles')}><span aria-hidden>╂</span> Kerzen</button></div><button className="po-marker-button" aria-pressed={events} onClick={()=>setEvents(!events)} title="Bestätigte Käufe und Verkäufe im Chart">↗ Trades</button>{(zoom[0]>0||zoom[1]<100)&&<button onClick={()=>setZoom([0,100])}>Zoom zurück</button>}</div>
    <div className="po-visuals"><div className="po-main-chart">
      <div className="po-chart-caption"><span><i className="po-key"/>{mode==='value'?'GESAMTES KONTO':'VERÄNDERUNG AB GEMEINSAMER MESSUNG'} · {cur}</span><span>{mode==='result'?'Zu-/Abflüsse bereinigt¹':view==='candles'?'Kontostand-OHLC¹':'Gemessene Kontostände'}</span></div>
      <div className="po-reference-bar">{mode==='value'?<><Toggle name="Cash" active={refs.cash} color="var(--dim)" onClick={()=>setRefs({...refs,cash:!refs.cash})}/><Toggle name="Ohne Bot · Modell" active={refs.hold} color="var(--amber)" onClick={()=>setRefs({...refs,hold:!refs.hold})}/></>:BOTS.map(b=><Toggle key={b.id} name={b.name} active={refs[b.id]} color={b.color} onClick={()=>setRefs({...refs,[b.id]:!refs[b.id]})}/>)}</div>
      <div className={`po-plot-wrap ${!ready?'loading':''}`}>
        {visible.length?<PortfolioPlot bars={visible} lines={model.lines} interval={d.interval} view={view} events={events?d.events:[]} currency={cur} range={range} animationKey={`${range}-${mode}`} onReset={()=>setZoom([0,100])} onZoom={(from,to)=>{
          const span=zoom[1]-zoom[0];setZoom([zoom[0]+from*span,zoom[0]+to*span]);
        }}/>:<div className="po-empty"><div className="po-empty-grid"/><span>◎</span><h3>{!ready?'Kontoverlauf wird geladen':mode==='result'?'Bot-Vergleich wird aufgebaut':'Noch keine Messwerte in diesem Zeitraum'}</h3><p>{poll.error|| (mode==='result'?'Historische Bot-Werte werden nicht rückwirkend erfunden.':'Neue Kontomessungen werden alle fünf Minuten gespeichert.')}</p><button onClick={reload}>Aktualisieren</button></div>}
      </div>
      <Navigator bars={model.bars} zoom={zoom} setZoom={setZoom}/>
      <div className="po-chart-bottom"><span>{visible.length?`${date(visible[0].start,true)} — ${date(visible.at(-1).ts,true)}`:'Noch keine Zeitreihe'}{visible.length===1?' · 1 Messpunkt':''}</span><span>{d?`${d.samples.toLocaleString('de-CH')} Messungen · ${d.interval<3600?`${d.interval/60} min`:`${d.interval/3600} h`}/Kerze`:'—'}</span></div>
    </div><aside className="po-allocation"><Allocation data={d} currency={cur}/><div className="po-extremes"><div><span>Periodenhoch</span><b>{money(summary.high)}</b></div><div><span>Periodentief</span><b>{money(summary.low)}</b></div><div><span>Erfasste Geldflüsse¹</span><b>{signed(summary.flows)}</b></div><div><span>Bereinigte Änderung¹</span><b className={tone(summary.net_change)}>{signed(summary.net_change)}</b></div></div></aside></div>
    <div className="po-bot-cards">{BOTS.map((b,i)=>{
      const pts=relativePoints(d,b.id),values=pts.filter(p=>finite(p[1])), last=values.at(-1)?.[1];
      return <button key={b.id} className={`po-bot-card ${mode==='result'&&refs[b.id]?'selected':''}`} style={{'--bot-color':b.color}} onClick={()=>{chooseMode('result');setRefs({...refs,[b.id]:true});}} aria-label={`${b.name} als Referenz anzeigen`}><div><span><i/>{b.name}<small>0{i+1}</small></span><b className={tone(last)}>{signed(last)} <small>{cur}</small></b><small>{values.length>1?'P/L-Änderung · inkl. offener Positionen':values.length?'Erster Messpunkt':'Historie beginnt mit dem Update'}</small></div><MiniLine points={pts} color={b.color} gap={Math.max(900,(d?.interval||300)*1.9)}/><span className="po-card-arrow">↗</span></button>;
    })}</div>
    <footer className="po-footer"><span>{d?.first?`Erfasst ab ${date(d.first,true)}`:'Echte Kontodaten · keine Demo'}{d?.available_since?` · Archiv seit ${date(d.available_since)}`:''}</span><details><summary>¹ Messung & Daten</summary><p>{d?.notes?.candles||'Kerzen basieren auf beobachteten Kontoständen.'} {d?.notes?.comparison} Die Kontowert-Änderung oben enthält Ein-/Auszahlungen; bereinigte Werte gelten ab {date(d?.baseline?.ts,true)}. Auto-erfasste Geldflüsse können unvollständig sein. {d?.notes?.legacy} Kurvenlücken werden nicht aufgefüllt. Ziehen im Chart zoomt; Pfeiltasten bewegen das Fadenkreuz.</p></details>{full&&<button className="po-native" onClick={nativeFull}>Browser-Vollbild</button>}</footer>
    {(poll.error||d?.status?.error)&&<div className="po-error" role="status">{poll.error||d.status.error} · Letzter gespeicherter Stand <button onClick={reload}>Neu laden</button></div>}
  </section>;
  return <>{!full&&content}<dialog ref={dialog} className="po-dialog" aria-label="Portfolio im Vollbild" onCancel={e=>{e.preventDefault();setFull(false);}}>{full&&content}</dialog></>;
}
function Toggle({name,color,active,onClick}){return <button className="po-reference" aria-pressed={active} onClick={onClick} style={{'--ref-color':color}}><i/>{name}</button>;}
function MiniLine({points,color,gap}){
  const ys=points.map(p=>p[1]).filter(finite);if(!ys.length)return <span className="po-mini-empty">—</span>;
  const min=Math.min(...ys),span=Math.max(...ys)-min||1,first=points[0][0],time=points.at(-1)[0]-first||1;
  const path=segments(points,gap).map(c=>c.map((p,i)=>`${i?'L':'M'}${2+(p[0]-first)/time*106},${36-(p[1]-min)/span*29}`).join(' ')).join(' ');
  return <svg className="po-mini" viewBox="0 0 110 42" aria-hidden><path d={path} fill="none" stroke={color} strokeWidth="1.8"/></svg>;
}
function Allocation({data,currency}){
  const list=data?.allocation||[],total=list.reduce((a,b)=>a+b.value,0);let offset=0;
  return <><div className="po-allocation-head"><span className="po-label">Verteilung jetzt</span><small>{data?.allocation_stale?'STAND PRÜFEN':'KONTO'}</small></div><div className="po-ring"><svg viewBox="0 0 160 160" aria-label="Aktuelle Kapitalverteilung" role="img"><circle className="po-ring-track" cx="80" cy="80" r="64"/>{list.filter(a=>a.value>0).map(a=>{const share=a.value/total*100,off=offset;offset+=share;return <circle key={a.id} cx="80" cy="80" r="64" pathLength="100" fill="none" stroke={colors[a.id]} strokeWidth="9" strokeDasharray={`${Math.max(0,share-.6)} ${100-Math.max(0,share-.6)}`} strokeDashoffset={-off} transform="rotate(-90 80 80)"><title>{labels[a.id]}: {money(a.value)} {currency}</title></circle>;})}</svg><div><strong>{total?money((list.find(a=>a.id==='cash')?.value||0)/total*100,0):'—'}<small>%</small></strong><span>CASH-ANTEIL</span></div></div><div className="po-allocation-list">{list.map(a=><div key={a.id}><span><i style={{background:colors[a.id]}}/>{labels[a.id]}</span><b>{money(a.value)}<small>{currency}</small></b></div>)}</div></>;
}
function Navigator({bars,zoom,setZoom}){
  if(bars.length<2)return null;
  const low=Math.min(...bars.map(b=>b.ohlc[3])),span=Math.max(...bars.map(b=>b.ohlc[3]))-low||1;
  const path=bars.map((b,i)=>`${i?'L':'M'}${i/(bars.length-1)*1000},${37-(b.ohlc[3]-low)/span*29}`).join(' ');
  return <div className="po-navigator"><svg viewBox="0 0 1000 46" preserveAspectRatio="none" aria-hidden><path d={path} fill="none" stroke="var(--dim)" strokeWidth="1"/><rect x={zoom[0]*10} width={(zoom[1]-zoom[0])*10} height="46" className="po-nav-window"/></svg><input aria-label="Zoom Anfang" type="range" min="0" max="100" step=".25" value={zoom[0]} onChange={e=>setZoom([Math.min(+e.target.value,zoom[1]-1),zoom[1]])}/><input aria-label="Zoom Ende" type="range" min="0" max="100" step=".25" value={zoom[1]} onChange={e=>setZoom([zoom[0],Math.max(+e.target.value,zoom[0]+1)])}/></div>;
}

function PortfolioPlot({bars,lines,interval,view,events,currency,range,onZoom,onReset,animationKey}){
  const box=useRef(null),[size,setSize]=useState([900,360]),[hover,setHover]=useState(null),[drag,setDrag]=useState(null);
  const uid=useId().replace(/:/g,'');
  useEffect(()=>{const ro=new ResizeObserver(([r])=>setSize([Math.max(200,r.contentRect.width),Math.max(220,r.contentRect.height)]));ro.observe(box.current);return()=>ro.disconnect();},[]);
  useEffect(()=>{setHover(null);setDrag(null);},[range,view,bars.length]);
  const [W,H]=size,L=12,R=W<550?64:82,T=20,B=36,pw=W-L-R,ph=H-T-B;
  const x0=bars[0].ts,x1=bars.at(-1).ts,dt=x1-x0||interval;
  const sx=ts=>L+(bars.length===1?.5:(ts-x0)/dt)*pw;
  const visibleLines=lines.map(l=>({...l,points:l.points.filter(p=>p[0]>=x0&&p[0]<=x1)}));
  const [y0,y1]=domain(bars,visibleLines,view==='candles'),sy=v=>T+(y1-v)/(y1-y0)*ph;
  const gap=Math.max(900,interval*1.9),paths=segments(bars.map(b=>[b.ts,b.ohlc[3]]),gap);
  const path=c=>c.map((p,i)=>`${i?'L':'M'}${sx(p[0]).toFixed(2)},${sy(p[1]).toFixed(2)}`).join(' ');
  const index=hover==null?-1:nearest(bars,x0+hover*dt),point=bars[index];
  const hX=point?sx(point.ts):0,last=bars.at(-1),lastY=sy(last.ohlc[3]);
  const candleW=Math.min(14,Math.max(1.2,pw/(bars.length+1)*.64));
  const pointer=e=>Math.max(0,Math.min(1,(e.clientX-e.currentTarget.getBoundingClientRect().left-L)/pw));
  const eventMap=new Map(events.map(e=>[e.bucket,e]));
  const markers=[];
  for(const b of bars){
    const event=eventMap.get(b.bucket);if(!event)continue;
    const previous=markers.at(-1),x=sx(b.ts);
    if(previous&&x-previous.anchor<20){previous.buys+=event.buys;previous.sells+=event.sells;previous.amount+=event.amount;previous.x=(previous.anchor+x)/2;}
    else markers.push({...event,anchor:x,x,y:Math.max(T+12,Math.min(T+ph-12,sy(b.ohlc[3])+20))});
  }
  const xfmt=ts=>new Date(ts*1000).toLocaleString('de-CH',dt<=86400?{hour:'2-digit',minute:'2-digit'}:{day:'2-digit',month:'short'});
  return <div ref={box} className="po-plot">
    <svg width={W} height={H} role="group" aria-label={`Interaktiver Kontograph in ${currency}. Pfeiltasten: Messwerte. Ziehen: Zoom.`} tabIndex="0" onKeyDown={e=>{
      if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?0:e.key==='End'?bars.length-1:Math.max(0,Math.min(bars.length-1,(index<0?bars.length-1:index)+(e.key==='ArrowRight'?1:-1)));setHover((bars[next].ts-x0)/dt);}
    }} onPointerMove={e=>{const x=pointer(e);setHover(x);if(drag)setDrag([drag[0],x]);}} onPointerLeave={()=>{if(!drag)setHover(null);}} onPointerDown={e=>{if(e.button!==0||e.pointerType==='touch')return;const x=pointer(e);e.currentTarget.setPointerCapture(e.pointerId);setDrag([x,x]);}} onPointerUp={()=>{if(drag&&Math.abs(drag[1]-drag[0])>.025)onZoom(Math.min(...drag),Math.max(...drag));setDrag(null);}} onPointerCancel={()=>setDrag(null)} onDoubleClick={onReset}>
      <defs><linearGradient id={`fill-${uid}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="var(--cyan)" stopOpacity=".15"/><stop offset="100%" stopColor="var(--cyan)" stopOpacity="0"/></linearGradient><clipPath id={`clip-${uid}`}><rect x={L-8} y={0} width={pw+16} height={H-B+1}/></clipPath></defs>
      {[0,.25,.5,.75,1].map(f=>{const y=T+ph*f,v=y1-(y1-y0)*f;return <g key={f}><line className="po-grid" x1={L} x2={W-R+6} y1={y} y2={y}/><text className="po-axis" x={W-R+15} y={y+4}>{money(v,Math.abs(v)>=10000?0:2)}</text></g>;})}
      {[0,.25,.5,.75,1].filter((f,i)=>W>550||i%2===0).map(f=><text key={f} className="po-axis" x={L+pw*f} y={H-9} textAnchor={f===0?'start':f===1?'end':'middle'}>{xfmt(x0+(x1-x0)*f)}</text>)}
      <g clipPath={`url(#clip-${uid})`}>
        <line x1={L} x2={W-R} y1={sy(bars[0].ohlc[0])} y2={sy(bars[0].ohlc[0])} className="po-baseline"/>
        {view==='line'?paths.map((c,i)=><g key={`${animationKey}-${i}`}><path d={`${path(c)} L${sx(c.at(-1)[0])},${T+ph} L${sx(c[0][0])},${T+ph}Z`} fill={`url(#fill-${uid})`}/><path className="po-main-line" d={path(c)} pathLength="1"/></g>):bars.map(b=><g className={`po-candle ${b.ohlc[3]>=b.ohlc[0]?'rising':'falling'}`} key={b.ts}><line x1={sx(b.ts)} x2={sx(b.ts)} y1={sy(b.ohlc[1])} y2={sy(b.ohlc[2])}/><rect x={sx(b.ts)-candleW/2} y={Math.min(sy(b.ohlc[0]),sy(b.ohlc[3]))} width={candleW} height={Math.max(1,Math.abs(sy(b.ohlc[0])-sy(b.ohlc[3])))}/></g>)}
        {visibleLines.map(l=><g key={l.id}>{segments(l.points,gap).map((c,i)=><path key={i} d={path(c)} fill="none" stroke={l.color} strokeWidth="1.65" strokeDasharray={l.id==='hold'?'5 5':undefined}/>)}</g>)}
        {markers.map(evt=><g key={evt.bucket} className="po-trade-marker" transform={`translate(${evt.x},${evt.y})`}><circle r="8"/><text y="3" textAnchor="middle">{evt.buys&&evt.sells?'↕':evt.buys?'↗':'↘'}</text><title>{evt.buys} Käufe · {evt.sells} Verkäufe · {money(evt.amount)} {currency} Umsatz</title></g>)}
        {drag&&<rect className="po-drag" x={L+Math.min(...drag)*pw} width={Math.abs(drag[1]-drag[0])*pw} y={T} height={ph}/>}
        <circle className="po-last-dot" cx={sx(last.ts)} cy={lastY} r="3.5"/>
        {point&&<g className="po-crosshair"><line x1={hX} x2={hX} y1={T} y2={H-B}/><line x1={L} x2={W-R} y1={sy(point.ohlc[3])} y2={sy(point.ohlc[3])}/><circle cx={hX} cy={sy(point.ohlc[3])} r="4"/></g>}
      </g>
      <g className="po-price-label" transform={`translate(${W-R+6},${Math.max(T+12,Math.min(H-B-12,point?sy(point.ohlc[3]):lastY))})`}><rect x="0" y="-11" width={R-8} height="22" rx="4"/><text x="5" y="4">{money(point?.ohlc[3]??last.ohlc[3],Math.abs(last.ohlc[3])>=10000?0:2)}</text></g>
    </svg>
    {point&&<div className="po-tooltip" style={{left:Math.min(W-(W<550?175:215),Math.max(4,hX+(hX>W*.55?-(W<550?184:224):16))),top:12}}><small>{date(point.start,true)}{point.start!==point.ts?` – ${new Date(point.ts*1000).toLocaleTimeString('de-CH',{hour:'2-digit',minute:'2-digit'})}`:''}</small><b>{money(point.ohlc[3])} {currency}</b>{view==='candles'&&<div className="po-ohlc">{['O','H','L','C'].map((k,i)=><span key={k}>{k}<strong>{money(point.ohlc[i])}</strong></span>)}</div>}{visibleLines.map(l=>{const v=l.points.find(p=>p[0]===point.ts)?.[1];return <div className="po-tooltip-row" key={l.id}><span style={{color:l.color}}>{l.name}</span><strong>{money(v)}</strong></div>;})}<small>{point.n} Kontomessung{point.n!==1?'en':''}{eventMap.has(point.bucket)?` · ${eventMap.get(point.bucket).buys} Käufe / ${eventMap.get(point.bucket).sells} Verkäufe`:''}</small></div>}
  </div>;
}
