// The right-hand inspector: trip chain (+detail), flag rows with accept/waive,
// the Resolved group, the legend, and the trip show/hide range controls.

import {S} from './state.js';
import {FC, FNAME, FDESC, FLONG, APPLY_VERB, APPLY_DONE, APPLY_RES, APPLY_HELP, WAIVE_HELP,
        tcol, flagSummary, flagRows, fmtDist} from './constants.js';
import {tripsEl, flagsEl, legendEl, rFrom, rTo} from './dom.js';
import {decide, manualMode} from './api.js';

const $ = id => document.getElementById(id);

const _ll = p => p ? p[0]+', '+p[1] : 'n/a';
const _row = (k,v) => `<div class="drow"><span class="k">${k}</span><span class="v">${v}</span></div>`;

// The Mode row is interactive: an arrow toggles a list of the workbook's modes
// (with their average speeds) so the reviewer can relabel this trip's mode.
function modeRowHTML(t){
 const open = S.modeOpen && S.sel===t.seq;
 const list = open ? `<div class="mode-list">`+S.modeSpeeds.map(m=>
    `<div class="mode-opt${m.mode===t.mode?' cur':''}" data-mode="${m.mode}">`+
    `<span>${m.mode}</span><span class="mode-sp">${m.avg_kmh} km/h</span></div>`).join('')+`</div>` : '';
 return `<div class="drow"><span class="k">Mode</span>`+
   `<span class="v mode-cur">${t.mode} <span class="mode-arrow">${open?'▾':'▸'}</span></span></div>`+list;
}

function detailHTML(t){
 return `<div class="detail">`+
  _row('Start',t.start)+_row('End',t.end)+_row('Duration',t.dur+' min')+
  modeRowHTML(t)+
  _row('Purpose',t.purpose||'n/a')+
  _row('Speed', t.speed!=null?t.speed+' km/h':'n/a')+
  _row('Distance travelled', fmtDist(t.dist_m))+
  _row('Displacement (start→end)', fmtDist(t.disp_m))+
  _row('Reported origin', _ll(t.s))+
  _row('Reported destination', _ll(t.e))+
  _row('GPS track start', _ll(t.r0))+
  _row('GPS track end', _ll(t.rN))+
 `</div>`;
}

function _mergeSelValid() {
 if(S.mergeSelected.size < 2) return false;
 const seqs = S.cur.data.trips
  .filter(t=>S.mergeSelected.has(t.id))
  .map(t=>t.seq)
  .sort((a,b)=>a-b);
 if(seqs.length !== S.mergeSelected.size) return false;
 for(let i=1; i<seqs.length; i++) if(seqs[i]!==seqs[i-1]+1) return false;
 return true;
}

function _updateMergeBar() {
 const bar = $('mergeBar');
 if(!S.mergeMode){ bar.style.display='none'; return; }
 bar.style.display='';
 const n = S.mergeSelected.size;
 const valid = _mergeSelValid();
 $('mergeHint').textContent = n===0 ? 'Click trips to select'
  : `${n} trip${n>1?'s':''} selected${valid?'':', must be consecutive'}`;
 const btn = $('bMergeCommit');
 btn.textContent = `Merge ${n} trip${n>1?'s':''}`;
 btn.disabled = !valid;
}

export function renderTrips(){
 const d = S.cur.data; tripsEl.innerHTML='';
 $('tcount').textContent = d.trips.length;
 $('nShown').textContent = (d.trips.length-S.hidden.size)+' / '+d.trips.length+' shown';
 d.trips.forEach(t=>{
  const card = document.createElement('div');
  const mergeSel = S.mergeMode && S.mergeSelected.has(t.id);
  card.className = 'row'+(t.seq===S.sel&&!S.mergeMode?' sel':'')
   +(S.hidden.has(t.seq)?' hidden':'')
   +(mergeSel?' merge-sel':'');
  const logged = t.manual?`<span class="logged">logged</span>`:'';
  card.innerHTML = `<div class="row__head">`+
   `<span class="node" style="background:${tcol(t.seq)}">${t.seq}</span>`+
   `<div class="row__main"><div class="row__time"><span class="mono">${t.start}–${t.end}</span>${logged}`+
   `<span class="mono" style="color:var(--slate-500)">${t.dur}m</span></div>`+
   `<div class="row__sub">${t.mode}${t.purpose?' · '+t.purpose:''}</div></div>`+
   (S.mergeMode ? '' : `<span class="vis${S.hidden.has(t.seq)?'':' on'}" title="Show/hide on map"></span>`)+
   `</div>`+
   (t.seq===S.sel&&!S.mergeMode?detailHTML(t):'');
  if(S.mergeMode){
   card.onclick = ()=>{
    S.mergeSelected.has(t.id)?S.mergeSelected.delete(t.id):S.mergeSelected.add(t.id);
    S.render(false);
   };
  } else {
   card.querySelector('.vis').onclick = e=>{e.stopPropagation(); S.hidden.has(t.seq)?S.hidden.delete(t.seq):S.hidden.add(t.seq); S.render();};
   card.onclick = ()=>{const on=S.sel!==t.seq; S.sel=on?t.seq:null; S.selId=on?t.id:null; S.modeOpen=false; S.render();};
   if(t.seq===S.sel){   // wire the interactive Mode row of the open detail
    const cur = card.querySelector('.mode-cur');
    if(cur) cur.onclick = e=>{e.stopPropagation(); S.modeOpen=!S.modeOpen; S.render();};
    card.querySelectorAll('.mode-opt').forEach(el=>el.onclick = e=>{
     e.stopPropagation();
     const m = el.dataset.mode; S.modeOpen=false;
     if(m!==t.mode) manualMode(t.id, m, S.cur.key); else S.render();
    });
   }
  }
  tripsEl.appendChild(card);
 });
 _updateMergeBar();
}

function flagDetailHTML(f){
 const rows = flagRows(f).map(([k,v,help])=>
   `<div class="drow" title="${help}"><span class="k">${k}</span><span class="v">${v}</span></div>`).join('');
 return `<div class="detail flagdetail">`+
   `<div class="fd-what">${FLONG[f.type]||''}</div>`+
   rows+
   `<div class="fd-act"><span class="fd-verb">${APPLY_VERB[f.type]}:</span> ${APPLY_HELP[f.type]||''}</div>`+
   `<div class="fd-act"><span class="fd-verb">Waive:</span> ${WAIVE_HELP[f.type]||''}</div>`+
  `</div>`;
}

export function flagCard(f){
 const sq = f.seqs.filter(x=>x!=null).map(s=>'#'+s).join(', ');
 const applyRes = APPLY_RES[f.type]||'accept';
 const acc = f.status==='accepted', wv = f.status==='waived';
 const open = S.flagSel===f.id;
 const card = document.createElement('div'); card.className='flagrow'+(open?' open':'');
 card.innerHTML = `<div class="flagrow__top"><span class="pill" style="background:${FC[f.type]}">${FNAME[f.type]}</span>`+
  `<span class="mono" style="font-size:11px;color:var(--slate-500)">${sq}</span>`+
  `<span class="status ${f.status}">${f.status}</span></div>`+
  `<div class="ev">${flagSummary(f)}<span class="ev-more">${open?'▾':'▸'}</span></div>`+
  (open?flagDetailHTML(f):'')+
  `<div class="flag-actions">`+
    `<button class="act apply${acc?' on':''}" title="${acc?'Click to undo':''}">${acc?APPLY_DONE[f.type]:APPLY_VERB[f.type]}</button>`+
    `<button class="act waive${wv?' on waive':''}" title="${wv?'Click to undo':''}">${wv?'Waived':'Waive'}</button></div>`;
 // Clicking the type/summary area toggles the expanded explanation.
 const toggle = ()=>{S.flagSel = open?null:f.id; renderFlags();};
 card.querySelector('.flagrow__top').onclick = toggle;
 card.querySelector('.ev').onclick = toggle;
 card.querySelector('.apply').onclick = ()=>{
  // Anchor selection to the trip that survives the merge (lowest raw_row of the
  // members) so the view stays put on what the user just acted on.
  if(!acc){
   const ids = f.seqs.filter(x=>x!=null)
     .map(s=>(S.cur.data.trips.find(t=>t.seq===s)||{}).id).filter(x=>x!=null);
   if(ids.length) S.selId = Math.min(...ids);
  }
  decide(f.id, acc?'clear':applyRes);
 };
 card.querySelector('.waive').onclick = ()=>decide(f.id, wv?'clear':'waive');
 return card;
}

export function renderFlags(){
 const d = S.cur.data;
 const pend = d.flags.filter(f=>f.status==='pending');
 const res = d.flags.filter(f=>f.status!=='pending');
 $('fcount').textContent = pend.length;
 flagsEl.innerHTML = '';
 if(!pend.length) flagsEl.innerHTML = '<div class="empty">No pending flags.</div>';
 else pend.forEach(f=>flagsEl.appendChild(flagCard(f)));

 const head = $('resolvedHead'), rEl = $('resolved');
 if(res.length){
  head.style.display = '';
  $('rcount').textContent = res.length;
  $('bResolvedToggle').textContent = S.resolvedOpen?'Hide':'Show';
  rEl.style.display = S.resolvedOpen?'':'none';
  rEl.innerHTML = '';
  if(S.resolvedOpen) res.forEach(f=>rEl.appendChild(flagCard(f)));
 }else{
  head.style.display='none'; rEl.style.display='none'; rEl.innerHTML='';
 }
}

export function renderLegend(){
 const types = [...new Set(S.cur.data.flags.map(f=>f.type))];
 legendEl.innerHTML = types.length ? types.map(ty=>
   `<div class="ft"><span class="pill" style="background:${FC[ty]}">${FNAME[ty]}</span><div class="desc">${FDESC[ty]}</div></div>`).join('')
   : '<div class="empty" style="padding:0">No flags on this day.</div>';
}

export function setRange(a,b){
 const T = S.cur.data.trips.length;
 a = Math.max(1,Math.min(T,a||1)); b = Math.max(a,Math.min(T,b||T));
 S.hidden = new Set(); S.cur.data.trips.forEach(t=>{if(t.seq<a||t.seq>b)S.hidden.add(t.seq);});
 rFrom.value=a; rTo.value=b; S.render();
}
export function showNone(){S.hidden=new Set(S.cur.data.trips.map(t=>t.seq)); rFrom.value=''; rTo.value=''; S.render();}
