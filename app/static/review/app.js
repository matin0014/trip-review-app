// Entry point: define the full render (map + panels), fill the S.render slot,
// wire the DOM event handlers, and kick off the initial load.

import {S, map} from './state.js';
import {drawMap, regionBounds} from './mapview.js';
import {renderTrips, renderFlags, renderEdits, renderLegend, setRange, showNone} from './inspector.js';
import {loadDays, fillDays, loadDay, rewind, manualMerge, manualAdd, manualReposition} from './api.js';
import {modeOptions, purposeOptions} from './constants.js';
import {personSel, daySel, lyTrips, lyStraight, lyFlags, lyEnds, lyHome, lyFollow, lyRegions, rFrom, rTo} from './dom.js';

const $ = id => document.getElementById(id);

// full render: redraw the map AND rebuild the inspector panels
function render(fit){
 if(!S.cur.data) return;
 drawMap(fit);
 renderTrips(); renderFlags(); renderEdits(); renderLegend();
}
S.render = render;

map.on('moveend', ()=>{if(S.cur.data) drawMap(false);});

// --- Add-trip mode: place a start then an end point, then enter times + mode ---
function exitAdd(){S.addMode=false; S.addPts=[]; $('addBar').style.display='none'; S.render(false);}
function updateAddBar(){
 const n=S.addPts.length, ready=n>=2;
 $('addHint').textContent = ready ? 'Set times, mode & purpose for the new trip'
   : (n===0 ? 'Click the trip start on the map' : 'Click the trip end on the map');
 $('addForm').style.display = ready ? '' : 'none';
}

// Both start and end time begin as auto-filled SUGGESTIONS, each derived from
// whichever of the two the reviewer typed first (real routed distance / the
// mode's canonical reference speed -- see server._estimate_duration_min). The
// moment the reviewer types into a field themselves, THAT field becomes
// theirs and stops being overwritten; if the OTHER field is still auto, it
// gets (re)derived from the one just typed. If both are manual (or both still
// empty), neither is touched by further suggestions or mode changes. The
// distance display is independent of all this -- it always shows once both
// points are placed.
let addStartAuto = true, addEndAuto = true;
function fmtDistance(m){
 return m<1000 ? `${Math.round(m)} m` : `${(m/1000).toFixed(1)} km`;
}
function shiftTime(hhmm, deltaMin){
 const [h,m] = hhmm.split(':').map(Number);
 const total = (((h*60 + m + deltaMin) % 1440) + 1440) % 1440;
 return `${String(Math.floor(total/60)).padStart(2,'0')}:${String(total%60).padStart(2,'0')}`;
}
async function updateRouteInfo(){
 if(S.addPts.length<2) return;
 const [s,en] = S.addPts;
 const q = new URLSearchParams({mode:$('addMode').value,
   start_lat:s[0], start_lon:s[1], end_lat:en[0], end_lon:en[1]});
 let data;
 try{ data = await (await fetch(`/api/route_estimate?${q}`)).json(); }
 catch(e){ $('addDistance').textContent=''; return; }  // offline/network hiccup
 $('addDistance').textContent = data.distance_m==null ? '' : fmtDistance(data.distance_m);
 if(data.duration_min==null) return;

 const dur = Math.round(data.duration_min);
 const st = $('addStartTime').value, et = $('addEndTime').value;
 if(addEndAuto && !addStartAuto && st) $('addEndTime').value = shiftTime(st, dur);
 else if(addStartAuto && !addEndAuto && et) $('addStartTime').value = shiftTime(et, -dur);
 // both auto (neither typed yet) or both manual: nothing to derive from/overwrite
}

function conflictingTrip(st, et){
 // st/et are "HH:MM" strings; trips carry the same format -- zero-padded, so
 // string compare is chronological within one day (matches the et<=st check
 // below). Strict overlap: touching endpoints (back-to-back trips) are fine.
 return (S.cur.data.trips||[]).find(t => st < t.end && t.start < et);
}

function addPoint(ll){
 if(S.addPts.length>=2) return;          // two points already placed; finish in the form
 S.addPts.push([ll.lat, ll.lng]);
 S.render(false); updateAddBar();
 if(S.addPts.length===2) updateRouteInfo();
}

// --- Reposition mode: pick "change start/end" on a trip, then click the map
// once to set the new point. One click, no separate commit step.
function exitReposition(){
 S.repositioning=null; $('repositionBar').style.display='none'; S.render(false);
}
S.exitReposition = exitReposition;
S.startReposition = (rawRow, end)=>{
 if(S.addMode){S.addMode=false; S.addPts=[]; $('addBar').style.display='none';}
 if(S.mergeMode){S.mergeMode=false; S.mergeSelected=new Set();}
 S.repositioning = {rawRow, end};
 $('repositionHint').textContent = `Click the map to set the new ${end} location`;
 $('repositionBar').style.display=''; S.render(false);
};
$('bRepositionCancel').onclick = exitReposition;

map.on('click', e=>{
 if(S.repositioning){
  const {rawRow, end} = S.repositioning;
  manualReposition(rawRow, end, e.latlng.lat, e.latlng.lng, S.cur.key);
  exitReposition();
  return;
 }
 if(S.addMode) addPoint(e.latlng);
});

$('bAdd').onclick = ()=>{
 S.mergeMode=false; S.mergeSelected=new Set(); exitReposition();
 S.addMode=true; S.addPts=[]; addStartAuto=true; addEndAuto=true;
 $('addMode').innerHTML = modeOptions('Walk');
 $('addPurpose').innerHTML = purposeOptions('');
 $('addStartTime').value=''; $('addEndTime').value=''; $('addDistance').textContent='';
 $('addBar').style.display=''; updateAddBar(); S.render(false);
};
$('bAddCancel').onclick = exitAdd;
$('addStartTime').oninput = ()=>{addStartAuto=false; updateRouteInfo();};
$('addEndTime').oninput = ()=>{addEndAuto=false; updateRouteInfo();};
$('addMode').onchange = updateRouteInfo;  // different mode -> different route/speed -> re-estimate
$('bAddCommit').onclick = ()=>{
 const st=$('addStartTime').value, et=$('addEndTime').value;
 if(S.addPts.length<2 || !st || !et){alert('Place start & end points and set both times.'); return;}
 if(et<=st){alert('End time must be after the start time.'); return;}  // HH:MM is zero-padded, so string compare is chronological
 const conflict = conflictingTrip(st, et);
 if(conflict){alert(`Overlaps trip #${conflict.seq} (${conflict.start}-${conflict.end}). Pick a different time.`); return;}
 const [s,en]=S.addPts;
 manualAdd({day_key:S.cur.key, start_time:st, end_time:et, mode:$('addMode').value,
   purpose:$('addPurpose').value,
   start_lat:s[0], start_lon:s[1], end_lat:en[0], end_lon:en[1]});
 exitAdd();
};

personSel.onchange = ()=>{if(S.addMode)exitAdd(); S.cur.person=personSel.value; fillDays();};
daySel.onchange = ()=>{if(S.addMode)exitAdd(); S.cur.key=daySel.value; loadDay();};
$('dayFilter').onchange = ()=>{S.dayFilter=$('dayFilter').value; fillDays();};
[lyTrips, lyStraight, lyFlags, lyEnds, lyHome].forEach(c=>c.onchange=render);
// Showing the region boxes zooms out to them (at a day's zoom the edges are off-screen).
lyRegions.onchange = ()=>{ if(lyRegions.checked) map.fitBounds(regionBounds(), {padding:[30,30]}); render(); };
lyFollow.onchange = ()=>{S.followSel=lyFollow.checked;};
$('bUndo').onclick = ()=>rewind('undo');
$('bRedo').onclick = ()=>rewind('redo');
$('bResolvedToggle').onclick = ()=>{S.resolvedOpen=!S.resolvedOpen; renderFlags();};
$('bEditedToggle').onclick = ()=>{S.editsOpen=!S.editsOpen; renderEdits();};
$('bNone').onclick = ()=>showNone();
$('bAll').onclick = ()=>setRange(1, S.cur.data.trips.length);
rFrom.onchange = ()=>setRange(parseInt(rFrom.value), parseInt(rTo.value));
rTo.onchange = ()=>setRange(parseInt(rFrom.value), parseInt(rTo.value));
$('bGenerate').onclick = async ()=>{
 // Inside the desktop app window, a plain download link does nothing (the
 // embedded view has no browser download UI) -- go through pywebview's own
 // Save As dialog instead. A plain browser tab (dev/testing) has no
 // window.pywebview, so it falls back to the old direct-download link.
 if(window.pywebview && window.pywebview.api){
  const r = await window.pywebview.api.save_export(window.SID);
  if(r.ok) alert(`Saved to ${r.path}`);
  else if(!r.cancelled) alert(`Export failed: ${r.error}`);
  return;
 }
 window.location.href = `/api/${window.SID}/generate`;
};
$('bMerge').onclick = ()=>{
 if(S.addMode){S.addMode=false; S.addPts=[]; $('addBar').style.display='none';}
 exitReposition();
 S.mergeMode=true; S.mergeSelected=new Set(); S.render(false);};
$('bMergeCancel').onclick = ()=>{S.mergeMode=false; S.mergeSelected=new Set(); S.render(false);};
$('bMergeCommit').onclick = ()=>{
 const rawRows=[...S.mergeSelected];
 S.mergeMode=false; S.mergeSelected=new Set();
 manualMerge(rawRows, S.cur.key);
};

loadDays();
