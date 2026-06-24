// The Leaflet layer: trip lines + direction arrows, numbered endpoints, and the
// pending problem-flags (drawn only when their trip is in the current viewport).
// Clicking a trip or flag opens a small action popup.
//
// Visual polylines are interactive:false so the polylineDecorator overlay doesn't
// block clicks. A transparent wider hit-zone polyline (interactive:true) sits on
// top to catch hover + click events reliably.

import {S, map, layer} from './state.js';
import {FC, FNAME, STRAIGHT_COL, tcol, APPLY_VERB, APPLY_DONE, APPLY_RES,
        modeOptions, hmToMin, minToHm} from './constants.js';
import {lyTrips, lyStraight, lyFlags, lyEnds} from './dom.js';
import {decide, manualDelete, manualSplit} from './api.js';

// Singleton Leaflet popup (no default close button; we provide our own).
const _popup = L.popup({closeButton: false, autoPan: true, className: 'map-popup'});

// Data associated with the currently-open popup (flag or trip object).
let _popupTarget = null;
let _popupKind = null; // 'flag' | 'trip' | 'split'

// Bind the data-action buttons of whatever is currently in the popup. Called on
// open AND after an in-place content swap (e.g. trip -> split form), so handlers
// always match the live DOM.
function _bind() {
 const el = _popup.getElement();
 if (!el) return;
 el.querySelectorAll('[data-action]').forEach(btn => {
  btn.onclick = e => { e.stopPropagation(); _onAction(btn); };
 });
}
map.on('popupopen', _bind);

function _swap(html) { _popup.setContent(html); _bind(); }

function _onAction(btn) {
 const action = btn.dataset.action;
 if (action === 'close') { _popup.close(); return; }
 if (_popupKind === 'flag') {
  decide(_popupTarget.id, btn.dataset.res);
  _popup.close();
 } else if (_popupKind === 'trip') {
  const t = _popupTarget;
  if (action === 'delete') {
   if (confirm(`Delete trip #${t.seq} (${t.mode}, ${t.start}–${t.end})?`)) manualDelete(t.id, S.cur.key);
   _popup.close();
  } else if (action === 'merge-enter') {
   S.mergeMode = true; S.mergeSelected = new Set([t.id]);
   _popup.close(); S.render(false);
  } else if (action === 'split-enter') {
   _popupKind = 'split'; _swap(_splitFormHtml(t));
  }
 } else if (_popupKind === 'split') {
  const t = _popupTarget, el = _popup.getElement();
  if (action === 'split-commit') {
   const at = el.querySelector('.sp-time').value;
   if (!at || at <= t.start || at >= t.end) {   // HH:MM zero-padded -> chronological string compare
    alert(`Split time must be between ${t.start} and ${t.end}.`); return;
   }
   manualSplit(t.id, at, el.querySelector('.sp-a').value, el.querySelector('.sp-b').value, S.cur.key);
   _popup.close();
  } else if (action === 'split-cancel') {
   _popupKind = 'trip'; _swap(_tripPopupHtml(t));
  }
 }
}

function _flagPopupHtml(f) {
 const applyRes = APPLY_RES[f.type] || 'accept';
 const acc = f.status === 'accepted', wv = f.status === 'waived';
 return `<div class="mp-head"><span class="pill" style="background:${FC[f.type]}">${FNAME[f.type]}</span>`+
  `<span class="mp-x" data-action="close">✕</span></div>`+
  `<div class="mp-acts">`+
  `<button class="act apply${acc?' on':''}" data-action="resolve" data-res="${acc?'clear':applyRes}">`+
   `${acc ? APPLY_DONE[f.type] : APPLY_VERB[f.type]}</button>`+
  `<button class="act waive${wv?' on waive':''}" data-action="resolve" data-res="${wv?'clear':'waive'}">`+
   `${wv ? 'Waived' : 'Waive'}</button>`+
  `</div>`;
}

function _tripPopupHtml(t) {
 return `<div class="mp-head"><span class="mono" style="font-size:12px">#${t.seq} ${t.mode}</span>`+
  `<span class="mp-x" data-action="close">✕</span></div>`+
  `<div class="mp-acts">`+
  `<button class="mp-del" data-action="delete">Delete</button>`+
  `<button class="mp-merge" data-action="merge-enter">Merge</button>`+
  `<button class="mp-split" data-action="split-enter">Split</button>`+
  `</div>`;
}

// Inline split editor: a time (pre-filled to the trip's midpoint) plus a mode for
// each resulting piece. A is the leg up to the split, B the leg after.
function _splitFormHtml(t) {
 const mid = minToHm((hmToMin(t.start) + hmToMin(t.end)) / 2);
 return `<div class="mp-head"><span class="mono" style="font-size:12px">Split #${t.seq}</span>`+
  `<span class="mp-x" data-action="split-cancel">✕</span></div>`+
  `<div class="mp-form">`+
  `<label>Split at <input class="sp-time" type="time" value="${mid}" min="${t.start}" max="${t.end}"></label>`+
  `<label>A mode <select class="sp-a">${modeOptions(t.mode)}</select></label>`+
  `<label>B mode <select class="sp-b">${modeOptions(t.mode)}</select></label>`+
  `<div class="mp-acts"><button class="act apply" data-action="split-commit">Split</button>`+
  `<button data-action="split-cancel">Cancel</button></div></div>`;
}

function showFlagPopup(f, latlng) {
 _popupTarget = f; _popupKind = 'flag';
 _popup.setLatLng(latlng).setContent(_flagPopupHtml(f)).openOn(map);
}

function showTripPopup(t, latlng) {
 _popupTarget = t; _popupKind = 'trip';
 _popup.setLatLng(latlng).setContent(_tripPopupHtml(t)).openOn(map);
}

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
  const mergeSel = S.mergeMode && S.mergeSelected.has(t.id);
  const col = lyStraight.checked ? STRAIGHT_COL
             : mergeSel ? '#f0c040'
             : tcol(t.seq);
  const selWeight = mergeSel ? 7 : (t.seq===S.sel ? 6 : 4);
  const opacity = mergeSel ? 1 : (t.seq===S.sel ? 1 : .85);

  if(lyTrips.checked){
   let line;
   if(lyStraight.checked){
    line = [t.s, t.e];
   }else{
    line = t.route.slice();
    const same = (a,b)=>a && b && a[0]===b[0] && a[1]===b[1];
    if(line.length && !same(line[0], t.s)) line.unshift(t.s);
    if(line.length && !same(line[line.length-1], t.e)) line.push(t.e);
   }

   // Visual polyline: interactive:false so the polylineDecorator overlay above
   // it does not absorb pointer events.
   const pl = L.polyline(line, {color:col, weight:selWeight, opacity, interactive:false}).addTo(layer);
   addArrows(pl, col);

   // Hit-zone polyline: wider transparent layer on top, interactive:true.
   // This is what the user actually hovers and clicks; it passes through visually.
   const hit = L.polyline(line, {color:col, weight:20, opacity:0.001}).addTo(layer);
   hit.on('click', e=>{
    if(S.addMode) return;   // let the map-level handler capture the add point
    L.DomEvent.stopPropagation(e);
    if(S.mergeMode){
     S.mergeSelected.has(t.id) ? S.mergeSelected.delete(t.id) : S.mergeSelected.add(t.id);
     S.render(false);
    } else {
     showTripPopup(t, e.latlng);
    }
   });

   line.forEach(p=>pts.push(p));
  }

  if(lyEnds.checked){
   L.marker(t.s, {icon:L.divIcon({className:'', iconSize:[22,22], html:`<div class="mapnode" style="background:${col}">${t.seq}</div>`})})
     .addTo(layer).on('click', e=>{
      if(S.addMode) return;
      L.DomEvent.stopPropagation(e);
      if(S.mergeMode){
       S.mergeSelected.has(t.id) ? S.mergeSelected.delete(t.id) : S.mergeSelected.add(t.id);
       S.render(false);
      } else {
       S.sel=t.seq; S.selId=t.id; S.modeOpen=false; S.render();
      }
     });
   L.circleMarker(t.e, {radius:5, color:col, weight:2, fillColor:'#fff', fillOpacity:1}).addTo(layer);
  }
 });

 if(S.addMode){   // temp markers for the trip being hand-placed (A start, B end)
  S.addPts.forEach((p,i)=>L.marker(p, {icon:L.divIcon({className:'', iconSize:[22,22],
    html:`<div class="mapnode addnode">${i?'B':'A'}</div>`})}).addTo(layer));
  if(S.addPts.length===2) L.polyline(S.addPts, {color:'#0a58ca', weight:3, dashArray:'6,5'}).addTo(layer);
 }

 if(fit && pts.length) map.fitBounds(pts, {padding:[50,50]});

 if(lyFlags.checked){
  const bnds = map.getBounds();
  d.flags.forEach(f=>{
   if(f.status!=='pending') return;
   const col = FC[f.type]||'#000', sq = f.seqs.filter(x=>x!=null); let mid;
   if(f.type==='zero_move'){
    const t = byseq[sq[0]]; if(!t || S.hidden.has(t.seq) || !bnds.contains(t.s)) return;
    // Circle marker is inherently a reasonable click target; keep interactive.
    const cm = L.circleMarker(t.s, {radius:12, color:col, weight:3, dashArray:'4', fill:false}).addTo(layer);
    cm.on('click', e=>{L.DomEvent.stopPropagation(e); showFlagPopup(f, t.s);});
    mid=t.s;
   }else{
    const a = byseq[sq[0]], b = byseq[sq[1]];
    if(!a || !b || S.hidden.has(a.seq) || S.hidden.has(b.seq)) return;
    if(!bnds.contains(a.e) && !bnds.contains(b.s)) return;
    const midLat = (a.e[0]+b.s[0])/2, midLon = (a.e[1]+b.s[1])/2;
    // Visual dashed flag line (non-interactive) + wide hit zone.
    L.polyline([a.e,b.s], {color:col, weight:3, dashArray:'8,6', interactive:false}).addTo(layer);
    const fhit = L.polyline([a.e,b.s], {color:col, weight:18, opacity:0.001}).addTo(layer);
    fhit.on('click', e=>{L.DomEvent.stopPropagation(e); showFlagPopup(f, [midLat,midLon]);});
    mid = [midLat, midLon];
   }
   const labelMarker = L.marker(mid, {interactive:true, icon:L.divIcon({className:'', iconSize:null, html:`<div class="flabel" style="background:${col}">${FNAME[f.type]}</div>`})}).addTo(layer);
   labelMarker.on('click', e=>{L.DomEvent.stopPropagation(e); showFlagPopup(f, mid);});
  });
 }
}
