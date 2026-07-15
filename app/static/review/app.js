// Entry point: define the full render (map + panels), fill the S.render slot,
// wire the DOM event handlers, and kick off the initial load.

import {S, map} from './state.js';
import {drawMap} from './mapview.js';
import {renderTrips, renderFlags, renderEdits, renderLegend, setRange, showNone} from './inspector.js';
import {loadDays, fillDays, loadDay, rewind, manualMerge, manualAdd} from './api.js';
import {COMMUTE_SPEEDS, MODES, hmToMin, minToHm} from './constants.js';
import {personSel, daySel, lyTrips, lyStraight, lyFlags, lyEnds, lyPlaces, lyFollow, rFrom, rTo} from './dom.js';

const $ = id => document.getElementById(id);

function fillSelect(el, values, selected='', blankLabel=null){
 el.innerHTML = '';
 if(blankLabel !== null) el.add(new Option(blankLabel, ''));
 [...new Set([selected, ...values].filter(Boolean))].forEach(v=>el.add(new Option(v, v)));
 el.value = selected;
}

function addModeValues(){
 return [...new Set([...S.modeSpeeds.map(m=>m.mode), ...MODES, ...Object.keys(COMMUTE_SPEEDS)])];
}

function commuteMode(raw){
 const s = String(raw || '').trim().toLowerCase();
 const aliases = {'bus':'Transit Bus', 'run':'Running', 'vehicle':'Vehicle (generic)',
  'transit':'Transit (generic)', 'ferry':'SeaBus'};
 if(aliases[s]) return aliases[s];
 return Object.keys(COMMUTE_SPEEDS).find(m=>m.toLowerCase()===s) || raw;
}

function haversineM(a, b){
 const R=6371008.8, rad=x=>x*Math.PI/180;
 const p1=rad(a[0]), p2=rad(b[0]), dp=rad(b[0]-a[0]), dl=rad(b[1]-a[1]);
 const h=Math.sin(dp/2)**2 + Math.cos(p1)*Math.cos(p2)*Math.sin(dl/2)**2;
 return 2*R*Math.asin(Math.min(1, Math.sqrt(h)));
}

let addAnchor = 'start';

function addDurationMin(){
 const mode = commuteMode($('addMode').value);
 const speed = COMMUTE_SPEEDS[mode];
 if(S.addPts.length<2 || !speed) return null;
 return (haversineM(S.addPts[0], S.addPts[1]) / 1000) / speed * 60;
}

function fillAddOtherTime(anchor){
 addAnchor = anchor || addAnchor;
 const minutes = addDurationMin();
 const st = $('addStartTime').value;
 const et = $('addEndTime').value;
 if(minutes == null){
  $('addTimeHint').textContent = 'no speed';
  return;
 }
 if(addAnchor === 'end' && et){
  $('addStartTime').value = minToHm(hmToMin(et) - minutes);
 }else if(st){
  $('addEndTime').value = minToHm(hmToMin(st) + minutes);
 }
 $('addTimeHint').textContent = `${Math.max(1, Math.round(minutes))} min`;
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
 renderTrips(); renderFlags(); renderEdits(); renderLegend();
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
 fillAddOtherTime(addAnchor);
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
 $('addStartTime').value='';
 $('addEndTime').value='';
 addAnchor = 'start';
 fillAddOtherTime(addAnchor);
 $('addBar').style.display=''; updateAddBar(); S.render(false);
};

window.addEventListener('open-add-trip', e=>{
 const d = e.detail || {};
 S.mergeMode=false; S.mergeSelected=new Set();
 S.addMode=true; S.addPts=(d.points || []).slice(0, 2);
 fillSelect($('addMode'), addModeValues(), d.mode || 'Auto Driver');
 fillSelect($('addPurpose'), S.purposeOptions, d.purpose || '', '(no purpose)');
 $('addStartTime').value=d.startTime || '';
 $('addEndTime').value=d.endTime || '';
 addAnchor = d.endTime && !d.startTime ? 'end' : 'start';
 $('addBar').style.display='';
 updateAddBar();
 S.render(false);
});
$('bAddCancel').onclick = exitAdd;
$('bAddCommit').onclick = async ()=>{
 const st=$('addStartTime').value, et=$('addEndTime').value;
 if(S.addPts.length<2 || !st || !et){alert('Place start & end points, then set start and end times.'); return;}
 if(et<=st){alert('End time must be after the start time.'); return;}  // HH:MM is zero-padded, so string compare is chronological
 const [s,en]=S.addPts;
 const mode = commuteMode($('addMode').value);
 const route = await networkRoute(s, en, mode);
 manualAdd({day_key:S.cur.key, start_time:st, end_time:et, mode,
   purpose:$('addPurpose').value, route,
   start_lat:s[0], start_lon:s[1], end_lat:en[0], end_lon:en[1]});
 exitAdd();
};
$('addStartTime').oninput = ()=>fillAddOtherTime('start');
$('addEndTime').oninput = ()=>fillAddOtherTime('end');
$('addMode').onchange = ()=>fillAddOtherTime(addAnchor);

personSel.onchange = ()=>{if(S.addMode)exitAdd(); S.cur.person=personSel.value; fillDays();};
daySel.onchange = ()=>{if(S.addMode)exitAdd(); S.cur.key=daySel.value; loadDay();};
[lyTrips, lyStraight, lyFlags, lyEnds, lyPlaces].forEach(c=>c.onchange=render);
lyFollow.onchange = ()=>{S.followSel=lyFollow.checked;};
$('bUndo').onclick = ()=>rewind('undo');
$('bRedo').onclick = ()=>rewind('redo');
$('bResolvedToggle').onclick = ()=>{S.resolvedOpen=!S.resolvedOpen; renderFlags();};
$('bEditedToggle').onclick = ()=>{S.editsOpen=!S.editsOpen; renderEdits();};
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
