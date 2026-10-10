/** Pure plotting math: preserve nulls/gaps; never synthesize missing observations. */
export const RANGES = [['1d','Tag'],['1w','Woche'],['1m','Monat'],['3m','3M'],['6m','6M'],['1y','Jahr'],['all','Alles']];
export const BOTS = [{id:'brain',name:'Daily',color:'#b69bff'}, {id:'fast',name:'Fast',color:'#42d8c5'}, {id:'volatility',name:'Volatility',color:'#ffb56b'}];
export const finite = n => typeof n === 'number' && Number.isFinite(n);
export const money = (n, digits=2) => finite(n) ? n.toLocaleString('de-CH',{minimumFractionDigits:digits,maximumFractionDigits:digits}) : '—';
export const signed = n => finite(n) ? `${n>0?'+':''}${money(n)}` : '—';
export function seriesFor(data, mode, refs) {
  const base=data?.baseline, bars=data?.bars||[];
  const main=bars.map(b=>{
    const ohlc=mode==='value'?b.account:b.net?.map(v=>v-base?.net);
    return {...b, ohlc:ohlc?.every(finite)?ohlc:null};
  }).filter(b=>b.ohlc);
  const lines=[];
  if(mode==='value') {
    if(refs.cash)lines.push({id:'cash',name:'Cash',color:'var(--dim)',points:main.map(b=>[b.ts,b.cash])});
    if(refs.hold)lines.push({id:'hold',name:'Ohne Bot · Modell',color:'var(--amber)',points:main.map(b=>[b.ts,b.hold])});
  } else for(const bot of BOTS)if(refs[bot.id])lines.push({...bot,points:main.map(b=>[b.ts,finite(b.bots?.[bot.id])&&finite(base?.bots?.[bot.id])?b.bots[bot.id]-base.bots[bot.id]:null])});
  return {bars:main,lines};
}
export function domain(bars, lines, candles) {
  const ys=bars.flatMap(b=>candles?b.ohlc:[b.ohlc[3]]).concat(lines.flatMap(l=>l.points.map(p=>p[1]))).filter(finite);
  let low=ys.length?Math.min(...ys):0, high=ys.length?Math.max(...ys):1;
  const pad=Math.max((high-low)*.13, Math.abs(high)*.0003, .02);
  return [low-pad,high+pad];
}
export function segments(points, gap) {
  const chunks=[];let chunk=[];
  for(const p of points){
    if(!finite(p[1])){if(chunk.length)chunks.push(chunk);chunk=[];continue;}
    if(chunk.length&&p[0]-chunk.at(-1)[0]>gap){chunks.push(chunk);chunk=[];}
    chunk.push(p);
  }
  if(chunk.length)chunks.push(chunk);
  return chunks;
}
export function nearest(points, ts) {
  if(!points.length)return -1;
  let lo=0,hi=points.length-1;
  while(lo<hi){const mid=Math.floor((lo+hi)/2);if(points[mid].ts<ts)lo=mid+1;else hi=mid;}
  return lo>0&&Math.abs(points[lo-1].ts-ts)<Math.abs(points[lo].ts-ts)?lo-1:lo;
}
export function viewport(bars, zoom) {
  if(bars.length<2)return bars;
  const start=Math.floor((bars.length-1)*zoom[0]/100),end=Math.ceil((bars.length-1)*zoom[1]/100);
  return bars.slice(start,Math.max(start+1,end)+1);
}
export function relativePoints(data, id) {
  const base=data?.baseline?.bots?.[id];
  return (data?.bars||[]).map(b=>[b.ts,finite(base)&&finite(b.bots?.[id])?b.bots[id]-base:null]);
}
