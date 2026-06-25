// Entry point: define the full render (map + panels), fill the S.render slot,
// wire the DOM event handlers, and kick off the initial load.

import {S, map} from './state.js';
import {drawMap} from './mapview.js';
import {renderTrips, renderFlags, renderLegend, setRange, showNone} from './inspector.js';
import {loadDays, fillDays, loadDay, rewind, manualMerge, manualAdd} from './api.js';
import {MODES} from './constants.js';
import {personSel, daySel, lyTrips, lyStraight, lyFlags, lyEnds, lyPlaces, lyFollow, rFrom, rTo} from './dom.js';

const $ = id => document.getElementById(id);

function fillSelect(el, values, selected='', blankLabel=null){
 el.innerHTML = '';
 if(blankLabel !== null) el.add(new Option(blankLabel, ''));
 [...new Set(values.filter(Boolean))].forEach(v=>el.add(new Option(v, v)));
 el.value = selected;
}

function addModeValues(){
 return [...new Set([...S.modeSpeeds.map(m=>m.mode), ...MODES])];
}

function routeProfile(mode){
 if(['Walk','Run'].includes(mode)) return 'foot';
 if(mode === 'Bike') return 'bike';
 return 'driving';
}

async function networkRoute(start, end, mode){
 const profiles = [...new Set([routeProfile(mode), 'driving'])];
 for(const profile of profiles){
  const url = `https://router.project-osrm.org/route/v1/${profile}/`+
   `${start[1]},${start[0]};${end[1]},${end[0]}?overview=full&geometries=geojson`;
  try{
   const r = await fetch(url);
   if(!r.ok) continue;
   const data = await r.json();
   const coords = data.routes?.[0]?.geometry?.coordinates;
   if(coords && coords.length >= 2) return coords.map(p=>[p[1], p[0]]);
  }catch(_err){}
 }
 return [start, end];
}

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
 $('addHint').textContent = ready ? 'Set times & mode for the new trip'
   : (n===0 ? 'Click the trip start on the map' : 'Click the trip end on the map');
 $('addForm').style.display = ready ? '' : 'none';
}
function addPoint(ll){
 if(S.addPts.length>=2) return;          // two points already placed; finish in the form
 S.addPts.push([ll.lat, ll.lng]);
 S.render(false); updateAddBar();
}
map.on('click', e=>{if(S.addMode) addPoint(e.latlng);});

$('bAdd').onclick = ()=>{
 S.mergeMode=false; S.mergeSelected=new Set();
 S.addMode=true; S.addPts=[];
 fillSelect($('addMode'), addModeValues(), 'Walk');
 fillSelect($('addPurpose'), S.purposeOptions, '', '(no purpose)');
 $('addStartTime').value=''; $('addEndTime').value='';
 $('addBar').style.display=''; updateAddBar(); S.render(false);
};
$('bAddCancel').onclick = exitAdd;
$('bAddCommit').onclick = async ()=>{
 const st=$('addStartTime').value, et=$('addEndTime').value;
 if(S.addPts.length<2 || !st || !et){alert('Place start & end points and set both times.'); return;}
 if(et<=st){alert('End time must be after the start time.'); return;}  // HH:MM is zero-padded, so string compare is chronological
 const [s,en]=S.addPts;
 const mode = $('addMode').value;
 const route = await networkRoute(s, en, mode);
 manualAdd({day_key:S.cur.key, start_time:st, end_time:et, mode,
   purpose:$('addPurpose').value, route,
   start_lat:s[0], start_lon:s[1], end_lat:en[0], end_lon:en[1]});
 exitAdd();
};

personSel.onchange = ()=>{if(S.addMode)exitAdd(); S.cur.person=personSel.value; fillDays();};
daySel.onchange = ()=>{if(S.addMode)exitAdd(); S.cur.key=daySel.value; loadDay();};
[lyTrips, lyStraight, lyFlags, lyEnds, lyPlaces].forEach(c=>c.onchange=render);
lyFollow.onchange = ()=>{S.followSel=lyFollow.checked;};
$('bUndo').onclick = ()=>rewind('undo');
$('bRedo').onclick = ()=>rewind('redo');
$('bResolvedToggle').onclick = ()=>{S.resolvedOpen=!S.resolvedOpen; renderFlags();};
$('bNone').onclick = ()=>showNone();
$('bAll').onclick = ()=>setRange(1, S.cur.data.trips.length);
rFrom.onchange = ()=>setRange(parseInt(rFrom.value), parseInt(rTo.value));
rTo.onchange = ()=>setRange(parseInt(rFrom.value), parseInt(rTo.value));
$('bGenerate').onclick = ()=>{window.location.href = `/api/${window.SID}/generate`;};
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
