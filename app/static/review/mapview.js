// The Leaflet layer: trip lines + direction arrows, numbered endpoints, and the
// pending problem-flags (drawn only when their trip is in the current viewport).

import {S, map, layer} from './state.js';
import {FC, FNAME, STRAIGHT_COL, tcol} from './constants.js';
import {lyTrips, lyStraight, lyFlags, lyEnds} from './dom.js';

export function addArrows(pl, color){
 const straight = lyStraight.checked;
 L.polylineDecorator(pl, {patterns:[{
   offset: straight?'50%':25, repeat: straight?0:120,
   symbol: L.Symbol.arrowHead({pixelSize:11, polygon:false, pathOptions:{stroke:true, weight:2, color, opacity:.95}})
 }]}).addTo(layer);
}

// map only — used on pan/zoom so the inspector panels are left untouched
export function drawMap(fit){
 const d = S.cur.data; if(!d) return;
 layer.clearLayers();
 const byseq = {}; d.trips.forEach(t=>byseq[t.seq]=t);
 const pts = [];
 d.trips.forEach(t=>{
  if(S.hidden.has(t.seq)) return;
  const col = lyStraight.checked ? STRAIGHT_COL : tcol(t.seq);
  if(lyTrips.checked){
   let line;
   if(lyStraight.checked){
    line = [t.s, t.e];
   }else{
    // close any gap between the GPS route ends and the declared start/end nodes
    line = t.route.slice();
    const same = (a,b)=>a && b && a[0]===b[0] && a[1]===b[1];
    if(line.length && !same(line[0], t.s)) line.unshift(t.s);
    if(line.length && !same(line[line.length-1], t.e)) line.push(t.e);
   }
   const pl = L.polyline(line, {color:col, weight:t.seq===S.sel?6:4, opacity:t.seq===S.sel?1:.85}).addTo(layer);
   addArrows(pl, col);
   line.forEach(p=>pts.push(p));
  }
  if(lyEnds.checked){
   L.marker(t.s, {icon:L.divIcon({className:'', iconSize:[22,22], html:`<div class="mapnode" style="background:${col}">${t.seq}</div>`})})
     .addTo(layer).on('click', ()=>{S.sel=t.seq; S.render();});
   L.circleMarker(t.e, {radius:5, color:col, weight:2, fillColor:'#fff', fillOpacity:1}).addTo(layer);
  }
 });
 if(fit && pts.length) map.fitBounds(pts, {padding:[50,50]});
 if(lyFlags.checked){
  const bnds = map.getBounds();   // only flags whose trips are in the current viewport
  d.flags.forEach(f=>{
   if(f.status!=='pending') return;   // decided flags (accepted/reviewed/waived) leave the map
   const col = FC[f.type]||'#000', sq = f.seqs.filter(x=>x!=null); let mid;
   if(f.type==='zero_move'){
    const t = byseq[sq[0]]; if(!t || S.hidden.has(t.seq) || !bnds.contains(t.s)) return;
    L.circleMarker(t.s, {radius:12, color:col, weight:3, dashArray:'4', fill:false}).addTo(layer); mid=t.s;
   }else{
    const a = byseq[sq[0]], b = byseq[sq[1]];
    if(!a || !b || S.hidden.has(a.seq) || S.hidden.has(b.seq)) return;
    if(!bnds.contains(a.e) && !bnds.contains(b.s)) return;
    L.polyline([a.e,b.s], {color:col, weight:3, dashArray:'8,6'}).addTo(layer);
    mid = [(a.e[0]+b.s[0])/2, (a.e[1]+b.s[1])/2];
   }
   L.marker(mid, {interactive:false, icon:L.divIcon({className:'', iconSize:null, html:`<div class="flabel" style="background:${col}">${FNAME[f.type]}</div>`})}).addTo(layer);
  });
 }
}
