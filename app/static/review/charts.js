// The "Start times" panel: an overlay line chart of trips by start hour,
// raw workbook vs the session's LIVE cleaned state (same projection the rest
// of the app shows). Data comes from GET /api/<sid>/start_times, which carries
// BOTH variants (all days / weekdays only) so the toggle never refetches; the
// chart is hand-rolled inline SVG, no chart library. While the panel is open,
// api.js pings chartsMaybeRefresh() after every action (via loadDay), and a
// short debounce coalesces bursts of edits into one recompute.

import {SID} from './state.js';

const $ = id => document.getElementById(id);

let mode = 'all';      // 'all' | 'weekday' -- which variant is displayed
let data = null;       // last /start_times response
let refreshTimer = null;

const W = 720, H = 320, M = {top: 16, right: 16, bottom: 44, left: 46};
const IW = W - M.left - M.right, IH = H - M.top - M.bottom;

function isOpen(){ return $('chartsModal').style.display !== 'none'; }

// "nice" y-axis ceiling: 1/2/5 * 10^k >= max, so gridlines land on round numbers
function niceMax(v){
 if(v <= 5) return 5;
 const pow = Math.pow(10, Math.floor(Math.log10(v)));
 for(const m of [1, 2, 5, 10]){ if(m * pow >= v) return m * pow; }
 return 10 * pow;
}

function line(pts){ return pts.map(p => p.join(',')).join(' '); }

function seriesSVG(bins, yMax, cls){
 const pts = bins.map((v, h) => [M.left + (h / 23) * IW, M.top + IH - (v / yMax) * IH]);
 const dots = pts.map(([x, y]) => `<circle class="${cls}" cx="${x}" cy="${y}" r="2.6"/>`).join('');
 return `<polyline class="${cls}" fill="none" points="${line(pts)}"/>` + dots;
}

function drawChart(){
 const el = $('chartSvg');
 if(!data){ el.innerHTML = '<div class="chart-empty">Loading…</div>'; return; }
 const v = data[mode];
 if(!v.n_raw){ el.innerHTML = '<div class="chart-empty">No trips in this session.</div>'; return; }
 const yMax = niceMax(Math.max(...v.raw, ...v.cleaned, 1));

 let g = '';
 const ySteps = 5;
 for(let i = 0; i <= ySteps; i++){                     // horizontal gridlines + y labels
  const val = Math.round(yMax * i / ySteps);
  const y = M.top + IH - (i / ySteps) * IH;
  g += `<line class="chart-grid" x1="${M.left}" y1="${y}" x2="${M.left + IW}" y2="${y}"/>` +
       `<text class="chart-lab" x="${M.left - 7}" y="${y + 3.5}" text-anchor="end">${val}</text>`;
 }
 for(let h = 0; h < 24; h += 2){                       // x labels every 2 hours
  const x = M.left + (h / 23) * IW;
  g += `<text class="chart-lab" x="${x}" y="${M.top + IH + 16}" text-anchor="middle">${h}</text>`;
 }
 g += `<text class="chart-lab" x="${M.left + IW / 2}" y="${H - 6}" text-anchor="middle">` +
      `hour of day (start time rounded to nearest hour)</text>`;
 g += seriesSVG(v.raw, yMax, 'chart-raw') + seriesSVG(v.cleaned, yMax, 'chart-cleaned');

 el.innerHTML =
  `<div class="chart-legend">` +
   `<span><i class="chart-swatch chart-swatch--raw"></i>raw (n=${v.n_raw})</span>` +
   `<span><i class="chart-swatch chart-swatch--cleaned"></i>cleaned (n=${v.n_cleaned})</span>` +
  `</div>` +
  `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Trip start times by hour">${g}</svg>`;
}

function fetchAndDraw(){
 fetch(`/api/${SID}/start_times`).then(r => r.json()).then(d => { data = d; drawChart(); });
}

// Called by api.js after every action lands: only recomputes while the panel
// is open, and debounced so a burst of edits costs one request.
export function chartsMaybeRefresh(){
 if(!isOpen()) return;
 clearTimeout(refreshTimer);
 refreshTimer = setTimeout(fetchAndDraw, 1500);
}

function setMode(m){
 mode = m;
 $('chartModeAll').classList.toggle('on', m === 'all');
 $('chartModeWeekday').classList.toggle('on', m === 'weekday');
 drawChart();
}

$('bCharts').onclick = () => { $('chartsModal').style.display = ''; data = null; drawChart(); fetchAndDraw(); };
$('bChartsClose').onclick = () => { $('chartsModal').style.display = 'none'; clearTimeout(refreshTimer); };
$('chartsModal').onclick = e => { if(e.target === $('chartsModal')) $('bChartsClose').onclick(); };
$('chartModeAll').onclick = () => setMode('all');
$('chartModeWeekday').onclick = () => setMode('weekday');
