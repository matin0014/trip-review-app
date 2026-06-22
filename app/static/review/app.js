// Entry point: define the full render (map + panels), fill the S.render slot,
// wire the DOM event handlers, and kick off the initial load.

import {S, map} from './state.js';
import {drawMap} from './mapview.js';
import {renderTrips, renderFlags, renderLegend, setRange, showNone} from './inspector.js';
import {loadDays, fillDays, loadDay, rewind} from './api.js';
import {personSel, daySel, lyTrips, lyStraight, lyFlags, lyEnds, rFrom, rTo} from './dom.js';

const $ = id => document.getElementById(id);

// full render: redraw the map AND rebuild the inspector panels
function render(fit){
 if(!S.cur.data) return;
 drawMap(fit);
 renderTrips(); renderFlags(); renderLegend();
}
S.render = render;

map.on('moveend', ()=>{if(S.cur.data) drawMap(false);});

personSel.onchange = ()=>{S.cur.person=personSel.value; fillDays();};
daySel.onchange = ()=>{S.cur.key=daySel.value; loadDay();};
[lyTrips, lyStraight, lyFlags, lyEnds].forEach(c=>c.onchange=render);
$('bUndo').onclick = ()=>rewind('undo');
$('bRedo').onclick = ()=>rewind('redo');
$('bResolvedToggle').onclick = ()=>{S.resolvedOpen=!S.resolvedOpen; renderFlags();};
$('bNone').onclick = ()=>showNone();
$('bAll').onclick = ()=>setRange(1, S.cur.data.trips.length);
rFrom.onchange = ()=>setRange(parseInt(rFrom.value), parseInt(rTo.value));
rTo.onchange = ()=>setRange(parseInt(rFrom.value), parseInt(rTo.value));
$('bGenerate').onclick = ()=>{window.location.href = `/api/${window.SID}/generate`;};

loadDays();
