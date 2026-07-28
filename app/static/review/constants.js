// Colours, labels, palettes, and pure formatting helpers. No DOM, no state.

export const FC = {fragment:'#c2479e', mode_change:'#8e44ad', missing_trip:'#2c6fb3', missing_activity:'#e07b1a', zero_move:'#138a72'};
export const FNAME = {fragment:'Fragment', mode_change:'Mode change', missing_trip:'Missing trip', missing_activity:'Missing activity', zero_move:'Zero-move'};
// Short tag under the pill (collapsed view).
export const FDESC = {
  fragment:'The app split one drive into pieces.', mode_change:'Different speed classes, kept separate.',
  missing_trip:'A real trip looks absent in the gap.', missing_activity:'A longer gap with an unlogged stop.', zero_move:'Logged trip that barely moved.'};
// Full plain-English explanation (expanded view).
export const FLONG = {
  fragment:'The app broke one continuous drive into separate pieces. They sit close together in space and time, so they are almost certainly the same trip.',
  mode_change:'These two trips were travelled at very different speeds (for example walking then driving). They might be one journey done by different modes, or genuinely separate trips.',
  missing_trip:'The gap between these trips took a little longer than the travel alone would need, so a short trip was probably made here but never logged.',
  missing_activity:'Far more time passed than the short distance needs, so the person was most likely stopped somewhere (an unlogged stop or activity) rather than travelling.',
  zero_move:'This logged trip barely changed location and moved very slowly, most likely GPS noise or a stationary period rather than a real trip.'};
// What the apply / waive buttons actually do, per flag type.
export const APPLY_HELP = {
  fragment:'merge the pieces into a single trip.', mode_change:'merge the two trips into one.',
  missing_trip:'insert a placeholder trip to fill the gap.',
  missing_activity:'mark the gap as a reviewed, unlogged stop (the trips are left unchanged).',
  zero_move:'remove this trip, since it did not really move.'};
export const WAIVE_HELP = {
  fragment:'keep them as two separate trips.', mode_change:'keep them as two separate trips.',
  missing_trip:'leave the gap as-is, with no placeholder trip.',
  missing_activity:'dismiss this flag.', zero_move:'keep this trip as it is.'};
export const APPLY_VERB = {fragment:'Accept', missing_trip:'Accept', zero_move:'Accept', missing_activity:'Reviewed', mode_change:'Merge'};
export const APPLY_DONE = {fragment:'Accepted', missing_trip:'Accepted', zero_move:'Accepted', missing_activity:'Reviewed', mode_change:'Merged'};
export const APPLY_RES = {fragment:'accept', missing_trip:'accept', zero_move:'accept', missing_activity:'reviewed', mode_change:'merge'};
export const TC = ["#e41a1c","#377eb8","#4daf4a","#984ea3","#ff7f00","#00bcd4","#a65628","#f781bf","#666633","#0d6b58","#b8860b","#7b68ee"];
export const STRAIGHT_COL = "#3d4759";   // straight start→end view: all trips one neutral colour

// Study-area boxes drawn on the map: the locked-in Vancouver area vs Central
// Okanagan boundaries for the out-of-region analysis. sw=[lat_lo,lon_lo],
// ne=[lat_hi,lon_hi]. Keep in sync with app.regions.DEFAULT_REGIONS (the count).
export const REGION_BOXES = [
  {name: "Vancouver area",   sw: [49.0, -123.29], ne: [49.39, -122.18], color: "#1558d6"},
  {name: "Central Okanagan", sw: [49.73, -119.79], ne: [50.51, -119.15], color: "#d9480f"},
];

// Canonical wave2 travel modes for the split/add dropdowns. The engine accepts any
// string (it keys on measured speed, not the label); an unknown original mode is
// prepended at render time so it stays selectable. Labels must match the survey
// vocabulary exactly: "Transit Bus" (never "Bus"), and no "Ferry" (the wave2
// dataset has none; the water mode it does have is "SeaBus").
export const MODES = ["Walk","Run","Bike","Auto Driver","Auto Passenger","Taxi",
  "Transit Bus","SkyTrain","SeaBus","Other","Unknown"];

// Canonical wave2 trip purposes for the purpose dropdown / add form. As with
// modes the engine never reads the label, so any value is accepted; an unknown
// original purpose is prepended at render time so it stays selectable.
export const PURPOSES = ["Returning Home","Work","School","Shopping","Recreation",
  "Social","Dine in at Restaurants/Cafe","Pick-up/drive-through at restaurants/cafe",
  "Pick-up/Drop-off family/friends","Travel to transit stop","Visiting Doctor/Dentist",
  "Visiting bank/library/Salon etc.","Working at local/neighborhood workspaces","Other"];
export function purposeOptions(sel){
  const list = PURPOSES.includes(sel)||!sel ? PURPOSES : [sel, ...PURPOSES];
  return list.map(p=>`<option value="${p}"${p===sel?' selected':''}>${p}</option>`).join('');
}

// "HH:MM" -> minutes-since-midnight and back, for the split midpoint default.
export function hmToMin(s){const [h,m]=s.split(':').map(Number);return h*60+m;}
export function minToHm(t){t=Math.max(0,Math.round(t));const h=Math.floor(t/60)%24,m=t%60;
  return String(h).padStart(2,'0')+':'+String(m).padStart(2,'0');}
export function modeOptions(sel){
  const list = MODES.includes(sel)||!sel ? MODES : [sel, ...MODES];
  return list.map(m=>`<option value="${m}"${m===sel?' selected':''}>${m}</option>`).join('');
}

// Personal-information rows for the left rail (label + the socio key), in display
// order. Only keys actually present on the person record are shown.
const _PINFO = [
  ['city', 'City'], ['vehicles', 'Vehicles'], ['age', 'Age'], ['gender', 'Gender'],
  ['hasLicense', "Driver's license"], ['hasTransitPass', 'Transit pass'],
  ['employmentStatus', 'Employment'], ['workArrangement', 'Work arrangement'],
];
// Some socio cells concatenate two respondents' answers as "A | B" (a shared
// accessCode covering two people). We only consider the FIRST, so show the part
// before the first '|'. No-op for plain values (city, vehicles, etc.).
const firstRespondent = v => (typeof v === 'string' && v.includes('|')) ? v.split('|')[0].trim() : v;

export function personInfoHTML(p){
  if(!p) return '';
  const rows = _PINFO.filter(([k])=>p[k]!=null && p[k]!=='')
    .map(([k,label])=>`<div class="pinfo-row"><span class="pinfo-k">${label}</span>`+
      `<span class="pinfo-v">${firstRespondent(p[k])}</span></div>`).join('');
  return rows ? `<div class="overline">Personal information</div>${rows}` : '';
}

const WD = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];

export function weekday(s){const a=s.split('-').map(Number);return WD[new Date(a[0],a[1]-1,a[2]).getDay()];}
export function tcol(seq){return TC[(seq-1)%TC.length];}

// Distance in human units: metres under 1 km, else km.
export function fmtDist(m){return m==null?'n/a':(m>=1000?(m/1000).toFixed(2)+' km':Math.round(m)+' m');}
function fmtMin(v){return v==null?'n/a':v+' min';}

// A plain-English one-liner for the collapsed flag row (no jargon).
export function flagSummary(f){
 const e=f.ev||{};
 switch(f.type){
  case 'fragment':      return `${fmtDist(e.gap_distance_m)} apart, ${fmtMin(e.gap_time_min)}, likely one drive split in two`;
  case 'missing_trip':  return `${fmtDist(e.gap_distance_m)} gap over ${fmtMin(e.gap_time_min)}, a short trip may be missing`;
  case 'missing_activity': return `${fmtMin(e.residual_min)} unaccounted for in a ${fmtMin(e.gap_time_min)} gap, likely a stop`;
  case 'mode_change':   return `speed jumps ${e.v_a_kmh} to ${e.v_b_kmh} km/h, different travel modes`;
  case 'zero_move':     return `moved only ${fmtDist(e.displacement_m)} at ${e.median_speed_kmh} km/h`;
  default: return '';
 }
}

// Labeled evidence rows for the expanded view: [label, value, plain meaning].
export function flagRows(f){
 const e=f.ev||{}, r=[];
 if(f.type==='zero_move'){
  if(e.displacement_m!=null) r.push(['Net displacement', fmtDist(e.displacement_m), 'straight-line distance from the trip’s start to its end']);
  if(e.median_speed_kmh!=null) r.push(['Median speed', e.median_speed_kmh+' km/h', 'typical speed while the device was moving']);
  return r;
 }
 if(e.gap_distance_m!=null) r.push(['Gap distance', fmtDist(e.gap_distance_m), 'straight-line distance between where the first trip ended and the next began']);
 if(e.gap_time_min!=null) r.push(['Gap time', fmtMin(e.gap_time_min), 'time elapsed between the two trips']);
 if(e.v_a_kmh!=null) r.push(['Speed of each trip', `${e.v_a_kmh}, then ${e.v_b_kmh} km/h`, 'median moving speed of the first trip, then the second trip in this flagged pair (the two trips listed by # above)']);
 if(e.t_min!=null) r.push(['Expected travel time', fmtMin(e.t_min), 'how long the gap would take at the first trip’s speed']);
 if(e.residual_min!=null) r.push(['Unaccounted time', fmtMin(e.residual_min), 'gap time minus expected travel time: the extra time the gap took beyond pure travel']);
 if(e.buffer_min!=null) r.push(['Tolerance', '± '+fmtMin(e.buffer_min), 'allowed slack around the expected travel time before the gap is treated as a stop']);
 return r;
}
