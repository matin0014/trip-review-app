// Server I/O: load the day index + a live day, post decisions / undo / redo.
// Every fetch ends by asking for a re-render via the S.render slot.

import {S, SID} from './state.js';
import {personSel, daySel, rFrom, rTo} from './dom.js';
import {weekday, personInfoHTML} from './constants.js';

const $ = id => document.getElementById(id);

export function loadDays(){
 fetch(`/api/${SID}/days`).then(r=>r.json()).then(d=>{
  const c = d.counts;
  $('dataset').textContent = `${d.raw_name}`;
  $('summary').textContent = `${c.trips} trips · ${c.flags} flags\n${c.persons} people · ${c.person_days} person-days`;
  S.people = d.people || {};
  S.modeSpeeds = d.mode_speeds || [];
  $('modespeeds').innerHTML = S.modeSpeeds.map(m=>
    `<div class="msrow"><span class="msmode">${m.mode}</span>`+
    `<span class="msval">${m.ref || 'n/a'}</span></div>`).join('')
    || '<div class="empty" style="padding:0">No modes.</div>';
  S.byPerson = {};
  d.days.forEach(x=>(S.byPerson[x.person]=S.byPerson[x.person]||[]).push(x));
  const ps = Object.keys(S.byPerson).sort();
  $('pcount').textContent = ps.length+' total';
  ps.forEach(p=>personSel.add(new Option(`${p}  (${S.byPerson[p].length}d)`, p)));
  S.cur.person = ps[0]; personSel.value = S.cur.person; fillDays();
 });
}

// ⚠ badge text for one day entry, shared by the initial fill and the live update.
const dayOptLabel = x => `${x.date} · ${weekday(x.date)}`+(x.n_flags?`  ⚠${x.n_flags}`:'');

export function fillDays(){
 daySel.innerHTML = '';
 S.byPerson[S.cur.person].slice().sort((a,b)=>a.date<b.date?-1:1).forEach(x=>
   daySel.add(new Option(dayOptLabel(x), x.key)));
 S.cur.key = daySel.value; loadDay();
}

// keep=true preserves the current show/hide + selection across a decision
// (a fresh day load resets them). fit=true re-frames the map (day/person switch).
export function loadDay(fit=true, keep=false){
 if(!keep){S.hidden=new Set(); S.sel=null; S.resolvedOpen=false;}
 fetch(`/api/${SID}/live-day?key=`+encodeURIComponent(S.cur.key)).then(r=>r.json()).then(d=>{
  S.cur.data = d;
  // keep the day dropdown's ⚠ badge in sync with the live pending count
  const entry = (S.byPerson[S.cur.person]||[]).find(x=>x.key===S.cur.key);
  if(entry){
   entry.n_flags = d.flags.filter(f=>f.status==='pending').length;
   const opt = [...daySel.options].find(o=>o.value===S.cur.key);
   if(opt) opt.textContent = dayOptLabel(entry);
  }
  if(keep){
   const T=d.trips.length; S.hidden=new Set([...S.hidden].filter(s=>s<=T));
   if(S.followSel && S.selId!=null){           // re-bind selection by stable id (raw_row)
    const hit = d.trips.find(t=>t.id===S.selId);
    S.sel = hit ? hit.seq : null; if(!hit) S.selId=null;
   } else if(S.sel!=null&&S.sel>T) S.sel=null;
  }
  else{rFrom.value=1; rTo.value=d.trips.length;}
  $('crumb').innerHTML = `<span class="mono">${S.cur.person}</span><span class="sep">/</span>`+
    `<span class="mono">${S.cur.key.split('|')[1]}</span><span class="sep">·</span>${d.trips.length} trips`;
  $('personinfo').innerHTML = personInfoHTML(S.people[S.cur.person]);
  S.render(fit);   // fit only on a fresh day load, never after an action
 });
}

export function decide(flagId, resolution){
 fetch(`/api/${SID}/decision`, {method:'POST', headers:{'Content-Type':'application/json'},
   body:JSON.stringify({flag_id:flagId, resolution})}).then(()=>loadDay(false,true));
}
export function rewind(path){fetch(`/api/${SID}/${path}`, {method:'POST'}).then(()=>loadDay(false,true));}

export function manualDelete(rawRow, dayKey){
 fetch(`/api/${SID}/manual_delete`, {method:'POST', headers:{'Content-Type':'application/json'},
   body:JSON.stringify({raw_row:rawRow, day_key:dayKey})}).then(()=>loadDay(false,true));
}
export function manualMode(rawRow, mode, dayKey){
 fetch(`/api/${SID}/manual_mode`, {method:'POST', headers:{'Content-Type':'application/json'},
   body:JSON.stringify({raw_row:rawRow, mode, day_key:dayKey})}).then(()=>loadDay(false,true));
}
export function manualPurpose(rawRow, purpose, dayKey){
 fetch(`/api/${SID}/manual_purpose`, {method:'POST', headers:{'Content-Type':'application/json'},
   body:JSON.stringify({raw_row:rawRow, purpose, day_key:dayKey})}).then(()=>loadDay(false,true));
}
export function manualTime(rawRow, startTime, endTime, dayKey){
 fetch(`/api/${SID}/manual_time`, {method:'POST', headers:{'Content-Type':'application/json'},
   body:JSON.stringify({raw_row:rawRow, start_time:startTime, end_time:endTime,
     day_key:dayKey})}).then(()=>loadDay(false,true));
}
export function manualMerge(rawRows, dayKey){
 S.selId = Math.min(...rawRows);   // select the merged result (its raw_row = min)
 fetch(`/api/${SID}/manual_merge`, {method:'POST', headers:{'Content-Type':'application/json'},
   body:JSON.stringify({raw_rows:rawRows, day_key:dayKey})}).then(()=>loadDay(false,true));
}

export function manualSplit(rawRow, splitTime, modeA, modeB, dayKey){
 fetch(`/api/${SID}/manual_split`, {method:'POST', headers:{'Content-Type':'application/json'},
   body:JSON.stringify({raw_row:rawRow, split_time:splitTime, mode_a:modeA, mode_b:modeB,
     day_key:dayKey})}).then(()=>loadDay(false,true));
}

export function manualAdd(payload){
 fetch(`/api/${SID}/manual_add`, {method:'POST', headers:{'Content-Type':'application/json'},
   body:JSON.stringify(payload)}).then(()=>loadDay(false,true));
}
