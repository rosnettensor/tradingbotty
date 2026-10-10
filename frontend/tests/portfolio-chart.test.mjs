import test from 'node:test';
import assert from 'node:assert/strict';
import {seriesFor,segments,nearest,viewport,domain,relativePoints} from '../src/portfolioChart.js';
const data={baseline:{ts:300,net:100,bots:{brain:2,fast:0,volatility:0}},bars:[
  {ts:0,account:[90,91,88,90],net:null,cash:null,bots:{brain:null}},
  {ts:300,account:[100,102,98,100],net:[100,102,98,100],cash:80,bots:{brain:2,fast:0,volatility:0}},
  {ts:600,account:[155,160,151,155],net:[105,110,101,105],cash:100,bots:{brain:5,fast:1,volatility:-1}}
]};
test('cash-adjusted comparison rebases all curves without fabricating legacy bots',()=>{
 const {bars,lines}=seriesFor(data,'result',{brain:true,fast:true});
 assert.equal(bars.length,2);assert.deepEqual(bars[1].ohlc,[5,10,1,5]);
 assert.deepEqual(lines[0].points,[[300,0],[600,3]]);
 assert.deepEqual(relativePoints(data,'brain'),[[0,null],[300,0],[600,3]]);
});
test('account value never gets confused with return or bot P/L',()=>{
 const m=seriesFor(data,'value',{cash:true,brain:true});
 assert.equal(m.bars.length,3);assert.equal(m.bars.at(-1).ohlc[3],155);
 assert.equal(m.lines.length,1);assert.equal(m.lines[0].id,'cash');
});
test('missing and stale intervals split chart paths',()=>{
 assert.deepEqual(segments([[0,1],[300,2],[600,null],[900,3],[2400,4]],900),[[[0,1],[300,2]],[[900,3]],[[2400,4]]]);
});
test('nearest sample, keyboard endpoints and zoom stay on recorded observations',()=>{
 assert.equal(nearest(data.bars,401),1);assert.equal(nearest(data.bars,9999),2);assert.equal(nearest([],-1),-1);
 assert.deepEqual(viewport(data.bars,[50,100]),data.bars.slice(1));
});
test('candle domains include sampled extrema, line domains use closes',()=>{
 const {bars}=seriesFor(data,'value',{});
 assert.ok(domain(bars,[],true)[1]>160);assert.ok(domain(bars,[],false)[0]<90);
 const flat=seriesFor({bars:[{ts:1,account:[1,1,1,1]}]},'value',{});
 const [low,high]=domain(flat.bars,[],true);assert.ok(low<1&&high>1);
});
