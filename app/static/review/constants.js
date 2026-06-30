// Colours, labels, palettes, and pure formatting helpers. No DOM, no state.

export const FC = {fragment:'#c2479e', mode_change:'#8e44ad', missing_trip:'#2c6fb3', missing_activity:'#e07b1a', zero_move:'#138a72', morning_commute:'#4767c2', purpose_work:'#756bb1', purpose_home:'#2ca25f'};
export const FNAME = {fragment:'Fragment', mode_change:'Mode change', missing_trip:'Missing trip', missing_activity:'Missing activity', zero_move:'Zero-move', morning_commute:'Morning commute', purpose_work:'Work purpose', purpose_home:'Home purpose'};
// Short tag under the pill (collapsed view).
export const FDESC = {
  fragment:'The app split one drive into pieces.', mode_change:'Different speed classes, kept separate.',
  missing_trip:'A real trip looks absent in the gap.', missing_activity:'A longer gap with an unlogged stop.', zero_move:'Logged trip that barely moved.',
  morning_commute:'A usual work commute may be missing before 10:00.',
  purpose_work:'Trip ends near work but has no purpose.', purpose_home:'Trip ends near home but has no purpose.'};
// Full plain-English explanation (expanded view).
export const FLONG = {
  fragment:'The app broke one continuous drive into separate pieces. They sit close together in space and time, so they are almost certainly the same trip.',
  mode_change:'These two trips were travelled at very different speeds (for example walking then driving). They might be one journey done by different modes, or genuinely separate trips.',
  missing_trip:'The gap between these trips took a little longer than the travel alone would need, so a short trip was probably made here but never logged.',
  missing_activity:'Far more time passed than the short distance needs, so the person was most likely stopped somewhere (an unlogged stop or activity) rather than travelling.',
  zero_move:'This logged trip barely changed location and moved very slowly, most likely GPS noise or a stationary period rather than a real trip.',
  morning_commute:'This person usually travels to work, but there is no trip before 10:00. The app can add a home-to-work commute for review.',
  purpose_work:'This trip has no purpose and ends within 200 m of the work location. The app can label it as Work.',
  purpose_home:'This trip has no purpose and ends within 200 m of the home location. The app can label it as Home.'};
// What the apply / waive buttons actually do, per flag type.
export const APPLY_HELP = {
  fragment:'merge the pieces into a single trip.', mode_change:'merge the two trips into one.',
  missing_trip:'insert a placeholder trip to fill the gap.',
  missing_activity:'mark the gap as a reviewed, unlogged stop (the trips are left unchanged).',
  zero_move:'remove this trip, since it did not really move.',
  morning_commute:'add a home-to-work commute starting at 08:30.',
  purpose_work:'set this trip purpose to Work.', purpose_home:'set this trip purpose to Home.'};
export const WAIVE_HELP = {
  fragment:'keep them as two separate trips.', mode_change:'keep them as two separate trips.',
  missing_trip:'leave the gap as-is, with no placeholder trip.',
  missing_activity:'dismiss this flag.', zero_move:'keep this trip as it is.',
  morning_commute:'do not add a commute.', purpose_work:'leave purpose blank.', purpose_home:'leave purpose blank.'};
export const APPLY_VERB = {fragment:'Accept', missing_trip:'Accept', zero_move:'Accept', missing_activity:'Reviewed', mode_change:'Merge', morning_commute:'Add trip', purpose_work:'Set Work', purpose_home:'Set Home'};
export const APPLY_DONE = {fragment:'Accepted', missing_trip:'Accepted', zero_move:'Accepted', missing_activity:'Reviewed', mode_change:'Merged', morning_commute:'Added', purpose_work:'Set', purpose_home:'Set'};
export const APPLY_RES = {fragment:'accept', missing_trip:'accept', zero_move:'accept', missing_activity:'reviewed', mode_change:'merge', morning_commute:'accept', purpose_work:'accept', purpose_home:'accept'};
export const TC = ["#e41a1c","#377eb8","#4daf4a","#984ea3","#ff7f00","#00bcd4","#a65628","#f781bf","#666633","#0d6b58","#b8860b","#7b68ee"];
export const STRAIGHT_COL = "#3d4759";   // straight start→end view: all trips one neutral colour

// Canonical wave2 travel modes for the split/add dropdowns. The engine accepts any
// string (it keys on measured speed, not the label); an unknown original mode is
// prepended at render time so it stays selectable.
export const MODES = ["Walk","Run","Bike","Auto Driver","Auto Passenger","Taxi",
  "Bus","SkyTrain","Ferry","Other","Unknown"];

export const COMMUTE_SPEEDS = {
  "Auto Driver": 38,
  "Auto Passenger": 38,
  "Bike": 17,
  "Car Share": 38,
  "Motorcycle": 40,
  "Running": 9,
  "Walk": 5,
  "Shared e-scooter": 18,
  "Taxi": 38,
  "Vehicle (generic)": 38,
  "Transit Bus": 20,
  "SkyTrain": 40,
  "SeaBus": 22,
  "Transit (generic)": 27,
};

// "HH:MM" -> minutes-since-midnight and back, for the split midpoint default.
export function hmToMin(s){const [h,m]=s.split(':').map(Number);return h*60+m;}
export function minToHm(t){t=Math.max(0,Math.round(t));const h=Math.floor(t/60)%24,m=t%60;
  return String(h).padStart(2,'0')+':'+String(m).padStart(2,'0');}
export function modeOptions(sel){
  const list = MODES.includes(sel)||!sel ? MODES : [sel, ...MODES];
  return list.map(m=>`<option value="${m}"${m===sel?' selected':''}>${m}</option>`).join('');
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
  case 'morning_commute': return `${fmtDist(e.gap_distance_m)} from home to work, no trip before 10:00`;
  case 'purpose_work':
  case 'purpose_home': return `ends ${fmtDist(e.displacement_m)} from ${e.place}, purpose is blank`;
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
 if(f.type==='morning_commute'){
  if(e.start_time) r.push(['Suggested start', e.start_time, 'fixed commute start time']);
  if(e.start) r.push(['Origin', e.start.join(', '), 'home coordinate']);
  if(e.end) r.push(['Destination', e.end.join(', '), 'work coordinate']);
  return r;
 }
 if(f.type==='purpose_work' || f.type==='purpose_home'){
  if(e.displacement_m!=null) r.push(['Distance to place', fmtDist(e.displacement_m), 'straight-line distance from trip end to this place']);
  if(e.buffer_m!=null) r.push(['Buffer', fmtDist(e.buffer_m), 'maximum distance for this purpose suggestion']);
  if(e.place) r.push(['Suggested purpose', e.place, 'purpose to apply if accepted']);
  return r;
 }
 if(e.gap_time_min!=null) r.push(['Gap time', fmtMin(e.gap_time_min), 'time elapsed between the two trips']);
 if(e.v_a_kmh!=null) r.push(['Speed of each trip', `${e.v_a_kmh}, then ${e.v_b_kmh} km/h`, 'median moving speed of the first trip, then the second trip in this flagged pair (the two trips listed by # above)']);
 if(e.t_min!=null) r.push(['Expected travel time', fmtMin(e.t_min), 'how long the gap would take at the first trip’s speed']);
 if(e.residual_min!=null) r.push(['Unaccounted time', fmtMin(e.residual_min), 'gap time minus expected travel time: the extra time the gap took beyond pure travel']);
 if(e.buffer_min!=null) r.push(['Tolerance', '± '+fmtMin(e.buffer_min), 'allowed slack around the expected travel time before the gap is treated as a stop']);
 return r;
}
