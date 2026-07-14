// Entry point: define the full render (map + panels), fill the S.render slot,
// wire the DOM event handlers, and kick off the initial load.

import {S, map} from './state.js';
import {drawMap, regionBounds} from './mapview.js';
import {renderTrips, renderFlags, renderLegend, setRange, showNone} from './inspector.js';
import {loadDays, fillDays, loadDay, rewind, manualMerge, manualAdd} from './api.js';
import {modeOptions, purposeOptions} from './constants.js';
import {personSel, daySel, lyTrips, lyStraight, lyFlags, lyEnds, lyHome, lyFollow, lyRegions, rFrom, rTo} from './dom.js';

const $ = id => document.getElementById(id);

// full render: redraw the map AND rebuild the inspector panels
function render(fit){
 if(!S.cur.data) return;
 drawMap(fit);
 renderTrips(); renderFlags(); renderLegend();
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

// End time starts as an auto-filled SUGGESTION (real routed distance / the
// mode's canonical reference speed -- see server._estimate_duration_min); the
// moment the reviewer types into the end-time field themselves, it becomes
// theirs and stops being overwritten by further suggestions. The distance
// display, unlike the end-time suggestion, always shows once both points are
// placed -- it doesn't depend on a start time being typed yet or on whether
// the reviewer has taken over the end-time field.
let addEndAuto = true;
function fmtDistance(m){
 return m<1000 ? `${Math.round(m)} m` : `${(m/1000).toFixed(1)} km`;
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

 if(!addEndAuto) return;
 const st = $('addStartTime').value;
 if(!st || data.duration_min==null) return;
 const [h,m] = st.split(':').map(Number);
 const total = (h*60 + m + Math.round(data.duration_min)) % (24*60);
 $('addEndTime').value = `${String(Math.floor(total/60)).padStart(2,'0')}:${String(total%60).padStart(2,'0')}`;
}

function addPoint(ll){
 if(S.addPts.length>=2) return;          // two points already placed; finish in the form
 S.addPts.push([ll.lat, ll.lng]);
 S.render(false); updateAddBar();
 if(S.addPts.length===2) updateRouteInfo();
}
map.on('click', e=>{if(S.addMode) addPoint(e.latlng);});

$('bAdd').onclick = ()=>{
 S.mergeMode=false; S.mergeSelected=new Set();
 S.addMode=true; S.addPts=[]; addEndAuto=true;
 $('addMode').innerHTML = modeOptions('Walk');
 $('addPurpose').innerHTML = purposeOptions('');
 $('addStartTime').value=''; $('addEndTime').value=''; $('addDistance').textContent='';
 $('addBar').style.display=''; updateAddBar(); S.render(false);
};
$('bAddCancel').onclick = exitAdd;
$('addStartTime').oninput = updateRouteInfo;
$('addEndTime').oninput = ()=>{addEndAuto=false;};
$('addMode').onchange = updateRouteInfo;  // different mode -> different route/speed -> re-estimate
$('bAddCommit').onclick = ()=>{
 const st=$('addStartTime').value, et=$('addEndTime').value;
 if(S.addPts.length<2 || !st || !et){alert('Place start & end points and set both times.'); return;}
 if(et<=st){alert('End time must be after the start time.'); return;}  // HH:MM is zero-padded, so string compare is chronological
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
 S.mergeMode=true; S.mergeSelected=new Set(); S.render(false);};
$('bMergeCancel').onclick = ()=>{S.mergeMode=false; S.mergeSelected=new Set(); S.render(false);};
$('bMergeCommit').onclick = ()=>{
 const rawRows=[...S.mergeSelected];
 S.mergeMode=false; S.mergeSelected=new Set();
 manualMerge(rawRows, S.cur.key);
};

loadDays();
