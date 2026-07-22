// The right-hand inspector: trip chain (+detail), flag rows with accept/waive,
// the Resolved group, the legend, and the trip show/hide range controls.

import {S} from './state.js';
import {FC, FNAME, FDESC, FLONG, APPLY_VERB, APPLY_DONE, APPLY_RES, APPLY_HELP, WAIVE_HELP,
        tcol, flagSummary, flagRows, fmtDist, PURPOSES} from './constants.js';
import {tripsEl, flagsEl, legendEl, rFrom, rTo} from './dom.js';
import {decide, manualMode, manualPurpose, manualTime, setComment, undoEdit} from './api.js';

const $ = id => document.getElementById(id);

const _row = (k,v) => `<div class="drow"><span class="k">${k}</span><span class="v">${v}</span></div>`;

// Comments are free reviewer text dropped into innerHTML, so they must be escaped.
const _esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

// The reviewer's free-text note for one trip, exported in the `comment` column.
// Collapsed: the note itself (with an edit link) or an "Add comment" button when
// there is none. Open: a textarea + Save/Cancel. Saving empty clears the note.
function commentHTML(t){
 const note = (S.cur.data.comments || {})[t.id] || '';
 if(S.commentOpen===t.seq){
  return `<div class="comment-edit">`+
    `<textarea class="cm-text" rows="3" placeholder="Note about this trip…">${_esc(note)}</textarea>`+
    `<div class="comment-act"><button class="cm-save">Save</button>`+
    `<button class="cm-cancel">Cancel</button></div></div>`;
 }
 if(note){
  return `<div class="drow comment-row"><span class="k">Comment</span>`+
    `<span class="v comment-toggle"><span class="comment-text">${_esc(note)}</span>`+
    ` <span class="comment-link">edit ▸</span></span></div>`;
 }
 return `<div class="drow comment-add"><button class="act comment-toggle">Add comment</button></div>`;
}

// The Mode row is interactive: an arrow toggles a list of the workbook's modes
// (with their reference speeds) so the reviewer can relabel this trip's mode.
function modeRowHTML(t){
 const open = S.modeOpen===t.seq;
 const list = open ? `<div class="mode-list">`+S.modeSpeeds.map(m=>
    `<div class="mode-opt${m.mode===t.mode?' cur':''}" data-mode="${m.mode}">`+
    `<span>${m.mode}</span><span class="mode-sp">${m.ref || ''}</span></div>`).join('')+`</div>` : '';
 return `<div class="drow"><span class="k">Mode</span>`+
   `<span class="v mode-cur">${t.mode} <span class="mode-arrow">${open?'▾':'▸'}</span></span></div>`+list;
}

// The Purpose row mirrors Mode: an arrow toggles the canonical purpose list so the
// reviewer can relabel this trip's purpose. The current purpose (if not canonical)
// is prepended so it stays selectable.
function purposeRowHTML(t){
 const open = S.purposeOpen===t.seq;
 const cur = t.purpose || 'n/a';
 const opts = (PURPOSES.includes(t.purpose)||!t.purpose) ? PURPOSES : [t.purpose, ...PURPOSES];
 const list = open ? `<div class="mode-list">`+opts.map(p=>
    `<div class="purpose-opt${p===t.purpose?' cur':''}" data-purpose="${p}"><span>${p}</span></div>`).join('')+`</div>` : '';
 return `<div class="drow"><span class="k">Purpose</span>`+
   `<span class="v purpose-cur">${cur} <span class="mode-arrow">${open?'▾':'▸'}</span></span></div>`+list;
}

// Start/End are display rows with an "Edit times" toggle; open -> two time inputs
// + Save/Cancel that re-stamp the trip's start/end (a manual_time op, which feeds
// the cascade since neighbour gaps depend on times).
function timeRowsHTML(t){
 if(S.timeOpen===t.seq){
  return `<div class="time-edit">`+
    `<label>Start <input type="time" class="tm-s" value="${t.start}"></label>`+
    `<label>End <input type="time" class="tm-e" value="${t.end}"></label>`+
    `<div class="time-edit-act"><button class="tm-save">Save</button>`+
    `<button class="tm-cancel">Cancel</button></div></div>`;
 }
 return _row('Start',t.start)+_row('End',t.end)+
   `<div class="drow time-toggle"><span class="k">Times</span>`+
   `<span class="v time-edit-link">edit ▸</span></div>`;
}

function detailHTML(t){
 // While repositioning THIS trip's start or end, that button becomes "Cancel"
 // (click it again, or click the map, to leave the mode) -- the other button
 // still switches the target endpoint if clicked mid-mode.
 const activeEnd = (S.repositioning && S.repositioning.rawRow===t.id) ? S.repositioning.end : null;
 return `<div class="detail">`+
  timeRowsHTML(t)+_row('Duration',t.dur+' min')+
  modeRowHTML(t)+
  purposeRowHTML(t)+
  _row('Speed', t.speed!=null?t.speed+' km/h':'n/a')+
  _row('Distance travelled', fmtDist(t.dist_m))+
  _row('Displacement (start→end)', fmtDist(t.disp_m))+
  _row('First GPS point', t.r0_t || 'n/a')+
  _row('Last GPS point', t.rN_t || 'n/a')+
  `<div class="drow reposition-act">`+
   `<button class="act reposition-btn${activeEnd==='start'?' reposition-cancel':''}" data-end="start">`+
    `${activeEnd==='start'?'Cancel':'Change start position'}</button>`+
   `<button class="act reposition-btn${activeEnd==='end'?' reposition-cancel':''}" data-end="end">`+
    `${activeEnd==='end'?'Cancel':'Change end position'}</button>`+
  `</div>`+
  (activeEnd ? `<div class="reposition-hint">Click the map to set the new ${activeEnd} location.</div>` : '')+
  commentHTML(t)+
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
  card.className = 'row'+(S.sel.has(t.seq)&&!S.mergeMode?' sel':'')
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
   (S.sel.has(t.seq)&&!S.mergeMode?detailHTML(t):'');
  if(S.mergeMode){
   card.onclick = ()=>{
    S.mergeSelected.has(t.id)?S.mergeSelected.delete(t.id):S.mergeSelected.add(t.id);
    S.render(false);
   };
  } else {
   // One delegated handler: which part of the card was clicked decides the action.
   card.onclick = (e)=>{
    if(e.target.closest('.vis')){
     S.hidden.has(t.seq)?S.hidden.delete(t.seq):S.hidden.add(t.seq); S.render(); return;
    }
    const opt = e.target.closest('.mode-opt');
    if(opt){
     const m = opt.dataset.mode; S.modeOpen=null;
     if(m!==t.mode) manualMode(t.id, m, S.cur.key); else S.render();
     return;
    }
    if(e.target.closest('.mode-cur')){ S.modeOpen = S.modeOpen===t.seq?null:t.seq; S.purposeOpen=null; S.render(); return; }
    const popt = e.target.closest('.purpose-opt');
    if(popt){
     const pr = popt.dataset.purpose; S.purposeOpen=null;
     if(pr!==t.purpose) manualPurpose(t.id, pr, S.cur.key); else S.render();
     return;
    }
    if(e.target.closest('.purpose-cur')){ S.purposeOpen = S.purposeOpen===t.seq?null:t.seq; S.modeOpen=null; S.render(); return; }
    if(e.target.closest('.tm-save')){
     const s = card.querySelector('.tm-s').value, en = card.querySelector('.tm-e').value;
     if(!s || !en) return;
     if(en <= s){ alert('End time must be after start time'); return; }   // HH:MM string compare = chronological within a day
     S.timeOpen=null; manualTime(t.id, s, en, S.cur.key); return;
    }
    if(e.target.closest('.tm-cancel')){ S.timeOpen=null; S.render(); return; }
    if(e.target.closest('.time-toggle')){ S.timeOpen = S.timeOpen===t.seq?null:t.seq; S.render(); return; }
    if(e.target.closest('.time-edit')) return;   // clicks inside the inputs don't collapse the detail
    const repBtn = e.target.closest('.reposition-btn');
    if(repBtn){
     const end = repBtn.dataset.end;
     const alreadyActive = S.repositioning && S.repositioning.rawRow===t.id && S.repositioning.end===end;
     alreadyActive ? S.exitReposition() : S.startReposition(t.id, end);
     return;
    }
    if(e.target.closest('.cm-save')){
     S.commentOpen=null; setComment(t.id, card.querySelector('.cm-text').value, S.cur.key); return;
    }
    if(e.target.closest('.cm-cancel')){ S.commentOpen=null; S.render(); return; }
    if(e.target.closest('.comment-toggle')){ S.commentOpen = S.commentOpen===t.seq?null:t.seq; S.render(); return; }
    if(e.target.closest('.comment-edit')) return;  // clicks inside the textarea don't collapse
    // Expanding a trip does NOT collapse the others -- several details can be
    // open at once; only the per-trip editors are exclusive.
    if(S.sel.has(t.seq)){ S.sel.delete(t.seq); S.selId.delete(t.id); }
    else { S.sel.add(t.seq); S.selId.add(t.id); }
    S.modeOpen=null; S.purposeOpen=null; S.timeOpen=null; S.commentOpen=null; S.render();
   };
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
   if(ids.length && ids.some(r=>S.selId.has(r))){
    ids.forEach(r=>S.selId.delete(r));
    S.selId.add(Math.min(...ids));   // follow the survivor; other trips stay expanded
   }
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

// The Edited group: every manual op logged for this day, oldest first, each
// with its own Undo (targets that ONE op via undo_event, unlike the toolbar
// Undo/Redo which only pops the most recent action off the LIFO).
export function renderEdits(){
 const edits = S.cur.data.edits || [];
 const head = $('editedHead'), el = $('edited');
 if(!edits.length){
  head.style.display='none'; el.style.display='none'; el.innerHTML='';
  return;
 }
 head.style.display = '';
 $('ecount').textContent = edits.length;
 $('bEditedToggle').textContent = S.editsOpen?'Hide':'Show';
 el.style.display = S.editsOpen?'':'none';
 el.innerHTML = '';
 if(!S.editsOpen) return;
 edits.slice().sort((a,b)=>(a.seq||0)-(b.seq||0)).forEach(edit=>{
  const row = document.createElement('div');
  row.className = 'editrow';
  const main = document.createElement('div');
  main.className = 'editrow__main';
  const seqSpan = document.createElement('span');
  seqSpan.className = 'mono'; seqSpan.textContent = `#${edit.seq}`;
  const labelSpan = document.createElement('span');
  labelSpan.textContent = edit.label;
  main.append(seqSpan, labelSpan);
  const btn = document.createElement('button');
  btn.type = 'button'; btn.textContent = 'Undo';
  btn.onclick = e=>{
   e.stopPropagation();
   if(confirm(`Undo edit #${edit.seq}?`)) undoEdit(edit.seq);
  };
  row.append(main, btn);
  el.appendChild(row);
 });
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
