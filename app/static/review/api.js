// Server I/O: load the day index + a live day, post decisions / undo / redo.
// Every fetch ends by asking for a re-render via the S.render slot.

import {S, SID} from './state.js';
import {personSel, daySel, rFrom, rTo} from './dom.js';
import {weekday, personInfoHTML} from './constants.js';
import {chartsMaybeRefresh} from './charts.js';

const $ = id => document.getElementById(id);

export function loadDays(){
 fetch(`/api/${SID}/days`).then(r=>r.json()).then(d=>{
  const c = d.counts;
  $('dataset').textContent = `${d.raw_name}`;
  $('summary').textContent = `${c.trips} trips · ${c.flags} flags\n${c.persons} people · ${c.person_days} person-days`;
  S.nExcluded = d.n_excluded || 0;
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
  ps.forEach(p=>personSel.add(new Option(personOptionLabel(p), p)));

  // Resume where the reviewer left off (last-opened person-day), falling back
  // to the first person if this is a fresh session or that person is gone.
  const lastKey = d.last_viewed && d.last_viewed.day_key;
  const lastPerson = lastKey ? lastKey.split('|')[0] : null;
  S.cur.person = (lastPerson && S.byPerson[lastPerson]) ? lastPerson : ps[0];
  if(S.cur.person === lastPerson) S.cur.key = lastKey;
  personSel.value = S.cur.person;
  fillDays();
 });
}

// ⚠ badge text for one day entry, shared by the initial fill and the live update.
// An out-of-region day removed from the export gets a ⊘ prefix; a day the
// reviewer has manually checked off gets a trailing ✓.
const dayOptLabel = x => (x.excluded?'⊘ ':'')+`${x.date} · ${weekday(x.date)}`+
  (x.n_flags?`  ⚠${x.n_flags}`:'')+(x.cleaned?'  ✓':'');

// A person is "done" once every ACTIVE (non-excluded) day of theirs is checked
// off -- an excluded day needs no cleaning, so it doesn't block the checkmark,
// but a person with every day excluded is not vacuously "done" either.
function personAllCleaned(person){
 const active = (S.byPerson[person]||[]).filter(x=>!x.excluded);
 return active.length>0 && active.every(x=>x.cleaned);
}
const personOptionLabel = p => `${p}  (${S.byPerson[p].length}d)`+(personAllCleaned(p)?'  ✓':'');

// Keep one person's option text (label + done-checkmark) in sync after a
// day_cleaned/day_include change.
function refreshPersonOption(person){
 const opt = [...personSel.options].find(o=>o.value===person);
 if(opt) opt.textContent = personOptionLabel(person);
}

// Day-list filter: which days the picker shows. "removed" carries the live count.
const DAY_FILTERS = {active:x=>!x.excluded, removed:x=>x.excluded, all:()=>true};
export function updateDayFilter(){
 const sel = $('dayFilter');
 if(!sel) return;
 const n = (S.byPerson[S.cur.person] || []).filter(x=>x.excluded).length;  // removed for THIS person
 sel.value = S.dayFilter;
 [...sel.options].forEach(o=>{ if(o.value==='removed') o.textContent = `Removed days (${n})`; });
}

// The per-day action bar under the picker. Removed day -> Recover; kept day -> a
// red Delete-day button (with a two-step confirm so a stray click can't drop a day).
function renderDayActions(entry){
 const bar = $('dayRegionBar');
 if(!entry){ bar.style.display='none'; bar.innerHTML=''; return; }
 bar.style.display = '';
 if(entry.excluded){            // removed (auto out-of-region OR hand-deleted) -> offer recover
  bar.className = 'region-bar removed';
  const lead = entry.out_of_region ? 'Leaves the study area — ' : '';
  bar.innerHTML = `<span class="region-msg">⊘ ${lead}<b>removed</b> from the export.</span>`+
                  '<button id="bRecoverDay" class="act apply">Recover day</button>';
  $('bRecoverDay').onclick = ()=>setDayIncluded(entry.key, true);
  return;
 }
 if(entry.out_of_region){       // kept but flagged: note it, still allow delete
  bar.className = 'region-bar kept';
  bar.innerHTML = '<span class="region-msg">⊘ Leaves the study area, but <b>kept</b> (recovered).</span>'+
                  '<button id="bDeleteDay" class="dangerbtn">Delete day</button>';
 } else {                       // ordinary in-region day: just the delete control
  bar.className = 'region-bar plain';
  bar.innerHTML = '<button id="bDeleteDay" class="dangerbtn">Delete day</button>';
 }
 $('bDeleteDay').onclick = ()=>confirmDelete(entry);
}

// Step two of the delete: swap the bar for an explicit confirm/cancel pair.
function confirmDelete(entry){
 const bar = $('dayRegionBar');
 bar.className = 'region-bar removed';
 bar.innerHTML = '<span class="region-msg"><b>Delete this day?</b> It is dropped from the export (recoverable later).</span>'+
                 '<button id="bDelYes" class="dangerbtn">Yes, delete</button>'+
                 '<button id="bDelNo" class="act">Cancel</button>';
 $('bDelYes').onclick = ()=>setDayIncluded(entry.key, false);
 $('bDelNo').onclick  = ()=>renderDayActions(entry);
}

export function fillDays(){
 daySel.innerHTML = '';
 updateDayFilter();   // refresh the "Removed days (N)" count for the current person
 const prev = S.cur.key;
 const pred = DAY_FILTERS[S.dayFilter] || DAY_FILTERS.active;
 S.byPerson[S.cur.person].slice().sort((a,b)=>a.date<b.date?-1:1).filter(pred)
   .forEach(x=> daySel.add(new Option(dayOptLabel(x), x.key)));
 if(!daySel.options.length){            // nothing matches the current filter for this person
  S.cur.key = null; S.cur.data = null;
  const msg = S.dayFilter==='removed' ? 'no removed days for this participant'
            : 'all days removed — switch the filter to “Removed days” to recover';
  $('crumb').innerHTML = `<span class="mono">${S.cur.person}</span><span class="sep">/</span>`+
    `<span class="muted">${msg}</span>`;
  renderDayActions(null);
  return;
 }
 if(prev && [...daySel.options].some(o=>o.value===prev)) daySel.value = prev;  // keep the open day
 S.cur.key = daySel.value; loadDay();
}

// Recover (include=true) or delete (false) one day, then refresh the picker.
export function setDayIncluded(dayKey, include){
 fetch(`/api/${SID}/day_include`, {method:'POST', headers:{'Content-Type':'application/json'},
   body:JSON.stringify({day_key:dayKey, include})}).then(()=>refreshDays());
}

// The reviewer's manual "cleaned" checkbox for one day, then refresh the picker
// (which also re-derives whether the WHOLE person is now done).
export function setDayCleaned(dayKey, cleaned){
 fetch(`/api/${SID}/day_cleaned`, {method:'POST', headers:{'Content-Type':'application/json'},
   body:JSON.stringify({day_key:dayKey, cleaned})}).then(()=>refreshDays());
}

// Re-pull the day index after a recover/delete/cleaned-toggle: update each
// entry's excluded / out_of_region / flag / cleaned state in place, refresh the
// open person's done-checkmark, then re-render the picker (keeping the open day).
export function refreshDays(){
 return fetch(`/api/${SID}/days`).then(r=>r.json()).then(d=>{
  S.nExcluded = d.n_excluded || 0;
  const byKey = {}; d.days.forEach(x=>byKey[x.key]=x);
  Object.values(S.byPerson).forEach(list=>list.forEach(e=>{
   const u = byKey[e.key];
   if(u){ e.excluded=u.excluded; e.out_of_region=u.out_of_region; e.n_flags=u.n_flags; e.cleaned=u.cleaned; }
  }));
  refreshPersonOption(S.cur.person);
  fillDays();
 });
}

// keep=true preserves the current show/hide + selection across a decision
// (a fresh day load resets them). fit=true re-frames the map (day/person switch).
export function loadDay(fit=true, keep=false){
 if(!keep){S.hidden=new Set(); S.sel=null; S.resolvedOpen=false; S.editsOpen=false;}
 fetch(`/api/${SID}/live-day?key=`+encodeURIComponent(S.cur.key)).then(r=>r.json()).then(d=>{
  S.cur.data = d;
  // keep the day dropdown's ⚠ badge in sync with the live pending count
  const entry = (S.byPerson[S.cur.person]||[]).find(x=>x.key===S.cur.key);
  if(entry){
   entry.n_flags = d.flags.filter(f=>f.status==='pending').length;
   const opt = [...daySel.options].find(o=>o.value===S.cur.key);
   if(opt) opt.textContent = dayOptLabel(entry);
  }
  $('dayCleaned').checked = !!(entry && entry.cleaned);
  renderDayActions(entry);   // recover / delete-day controls for the open day
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
  chartsMaybeRefresh();   // start-times panel follows along while open
 });
}

export function decide(flagId, resolution){
 fetch(`/api/${SID}/decision`, {method:'POST', headers:{'Content-Type':'application/json'},
   body:JSON.stringify({flag_id:flagId, resolution})}).then(()=>loadDay(false,true));
}
export function rewind(path){fetch(`/api/${SID}/${path}`, {method:'POST'}).then(()=>loadDay(false,true));}
export function undoEdit(seq){
 fetch(`/api/${SID}/undo_event`, {method:'POST', headers:{'Content-Type':'application/json'},
   body:JSON.stringify({seq})}).then(()=>loadDay(false,true));
}

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

export function manualReposition(rawRow, end, lat, lon, dayKey){
 fetch(`/api/${SID}/manual_reposition`, {method:'POST', headers:{'Content-Type':'application/json'},
   body:JSON.stringify({raw_row:rawRow, end, lat, lon, day_key:dayKey})}).then(()=>loadDay(false,true));
}
