// The right-hand inspector: trip chain (+detail), flag rows with accept/waive,
// the Resolved group, the legend, and the trip show/hide range controls.

import {S} from './state.js';
import {FC, FNAME, FDESC, APPLY_VERB, APPLY_DONE, APPLY_RES, tcol, evLine} from './constants.js';
import {tripsEl, flagsEl, legendEl, rFrom, rTo} from './dom.js';
import {decide} from './api.js';

const $ = id => document.getElementById(id);

function detailHTML(t){
 const rows = [['Start',t.start], ['End',t.end], ['Duration',t.dur+' min'],
  ['Mode',t.mode], ['Purpose',t.purpose||'—'],
  ['Speed', t.speed!=null?t.speed+' km/h':'—'],
  ['Origin', t.s[0]+', '+t.s[1]]];
 return `<div class="detail">`+rows.map(([k,v])=>`<div class="drow"><span class="k">${k}</span><span class="v">${v}</span></div>`).join('')+`</div>`;
}

export function renderTrips(){
 const d = S.cur.data; tripsEl.innerHTML='';
 $('tcount').textContent = d.trips.length;
 $('nShown').textContent = (d.trips.length-S.hidden.size)+' / '+d.trips.length+' shown';
 d.trips.forEach(t=>{
  const card = document.createElement('div');
  card.className = 'row'+(t.seq===S.sel?' sel':'')+(S.hidden.has(t.seq)?' hidden':'');
  const logged = t.manual?`<span class="logged">logged</span>`:'';
  card.innerHTML = `<div class="row__head">`+
   `<span class="node" style="background:${tcol(t.seq)}">${t.seq}</span>`+
   `<div class="row__main"><div class="row__time"><span class="mono">${t.start}–${t.end}</span>${logged}`+
   `<span class="mono" style="color:var(--slate-500)">${t.dur}m</span></div>`+
   `<div class="row__sub">${t.mode}${t.purpose?' · '+t.purpose:''}</div></div>`+
   `<span class="vis${S.hidden.has(t.seq)?'':' on'}" title="Show/hide on map"></span></div>`+
   (t.seq===S.sel?detailHTML(t):'');
  card.querySelector('.vis').onclick = e=>{e.stopPropagation(); S.hidden.has(t.seq)?S.hidden.delete(t.seq):S.hidden.add(t.seq); S.render();};
  card.onclick = ()=>{S.sel=(S.sel===t.seq?null:t.seq); S.render();};
  tripsEl.appendChild(card);
 });
}

export function flagCard(f){
 const sq = f.seqs.filter(x=>x!=null).map(s=>'#'+s).join(', ');
 const applyRes = APPLY_RES[f.type]||'accept';
 const acc = f.status==='accepted', wv = f.status==='waived';
 const card = document.createElement('div'); card.className='flagrow';
 card.innerHTML = `<div class="flagrow__top"><span class="pill" style="background:${FC[f.type]}">${FNAME[f.type]}</span>`+
  `<span class="mono" style="font-size:11px;color:var(--slate-500)">${sq}</span>`+
  `<span class="status ${f.status}">${f.status}</span></div><div class="ev">${evLine(f.ev||{})}</div>`+
  `<div class="flag-actions">`+
    `<button class="act apply${acc?' on':''}" title="${acc?'Click to undo':''}">${acc?APPLY_DONE[f.type]:APPLY_VERB[f.type]}</button>`+
    `<button class="act waive${wv?' on waive':''}" title="${wv?'Click to undo':''}">${wv?'Waived':'Waive'}</button></div>`;
 card.querySelector('.apply').onclick = ()=>decide(f.id, acc?'clear':applyRes);
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
