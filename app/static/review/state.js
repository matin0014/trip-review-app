// Shared singletons: the Leaflet map + draw layer, the session id, and the one
// mutable state bag every module reads/writes. `render` is a slot app.js fills at
// startup so any module can request a re-render without a circular import.

export const SID = window.SID;

export const map = L.map('map', {zoomControl:true}).setView([49.26,-123.1], 12);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19, attribution:'© OpenStreetMap'}).addTo(map);
export const layer = L.layerGroup().addTo(map);

export const S = {
  byPerson: {},
  dayFilter: 'active',     // day-picker filter: 'active' | 'removed' | 'all'
  nExcluded: 0,            // count of days dropped from the export (out of region + deleted)
  people: {},              // accessCode -> {home,work,vehicles,city} (optional SD)
  modeSpeeds: [],          // [{mode, avg_kmh, n}] workbook-wide reference
  cur: {person:null, key:null, data:null},
  hidden: new Set(),       // seqs toggled off
  sel: new Set(),          // seqs of trips whose detail is expanded (several at once)
  // Editors are scoped to ONE trip at a time: each holds that trip's seq, or null.
  // (Details can be expanded on several trips at once; only one editor is open.)
  modeOpen: null,          // seq of the trip whose mode dropdown is expanded
  timeOpen: null,          // seq of the trip whose start/end time editor is open
  commentOpen: null,       // seq of the trip whose comment textarea is open
  purposeOpen: null,       // seq of the trip whose purpose dropdown is expanded
  flagSel: null,           // expanded flag id (detail open)
  selId: new Set(),        // those trips' raw_rows (stable ids, survive renumber)
  followSel: true,         // keep the selection on its trip across an action
  resolvedOpen: false,     // is the Resolved group expanded
  editsOpen: false,        // is the Edited group expanded
  mergeMode: false,        // are we in trip-merge selection mode?
  mergeSelected: new Set(),// raw_rows (t.id) chosen for merge
  addMode: false,          // are we placing a new (manual_add) trip?
  addPts: [],              // [[lat,lon], ...] clicked while adding (start, then end)
  repositioning: null,     // {rawRow, end} while waiting for a map click to move a trip's start/end
  render: null,            // (fit) => void — assigned by app.js
};
