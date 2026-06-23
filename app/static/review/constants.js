// Colours, labels, palettes, and pure formatting helpers. No DOM, no state.

export const FC = {fragment:'#c2479e', mode_change:'#8e44ad', missing_trip:'#2c6fb3', missing_activity:'#e07b1a', zero_move:'#138a72'};
export const FNAME = {fragment:'Fragment', mode_change:'Mode change', missing_trip:'Missing trip', missing_activity:'Missing activity', zero_move:'Zero-move'};
export const FDESC = {
  fragment:'The app split one drive into pieces.', mode_change:'Different speed classes — kept separate.',
  missing_trip:'A real trip looks absent in the gap.', missing_activity:'A longer gap — an unlogged stop.', zero_move:'Logged trip that barely moved.'};
export const APPLY_VERB = {fragment:'Accept', missing_trip:'Accept', zero_move:'Accept', missing_activity:'Reviewed', mode_change:'Merge'};
export const APPLY_DONE = {fragment:'Accepted', missing_trip:'Accepted', zero_move:'Accepted', missing_activity:'Reviewed', mode_change:'Merged'};
export const APPLY_RES = {fragment:'accept', missing_trip:'accept', zero_move:'accept', missing_activity:'reviewed', mode_change:'merge'};
export const TC = ["#e41a1c","#377eb8","#4daf4a","#984ea3","#ff7f00","#00bcd4","#a65628","#f781bf","#666633","#0d6b58","#b8860b","#7b68ee"];
export const STRAIGHT_COL = "#3d4759";   // straight start→end view: all trips one neutral colour

const WD = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];

export function weekday(s){const a=s.split('-').map(Number);return WD[new Date(a[0],a[1]-1,a[2]).getDay()];}
export function tcol(seq){return TC[(seq-1)%TC.length];}

export function evLine(e){
 if(e.median_speed_kmh!=null)return `${e.displacement_m!=null?e.displacement_m+' m':''}${e.median_speed_kmh!=null?' · '+e.median_speed_kmh+' km/h':''}`;
 const s=[];
 if(e.gap_distance_m!=null)s.push(e.gap_distance_m+' m');
 if(e.gap_time_min!=null)s.push(e.gap_time_min+' min');
 if(e.v_a_kmh!=null)s.push(e.v_a_kmh+(e.v_b_kmh!=null?'→'+e.v_b_kmh:'')+' km/h');
 if(e.residual_min!=null)s.push('resid '+e.residual_min+(e.buffer_min!=null?'/buf '+e.buffer_min:''));
 return s.join(' · ');
}
