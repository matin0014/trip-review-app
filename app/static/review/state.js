// Shared singletons: the Leaflet map + draw layer, the session id, and the one
// mutable state bag every module reads/writes. `render` is a slot app.js fills at
// startup so any module can request a re-render without a circular import.

export const SID = window.SID;

export const map = L.map('map', {zoomControl:true}).setView([49.26,-123.1], 12);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19, attribution:'© OpenStreetMap'}).addTo(map);
export const layer = L.layerGroup().addTo(map);

export const S = {
  byPerson: {},
  personInfo: {},          // {accessCode: [{label, value}, ...]}
  modeSpeeds: [],          // [{mode, avg_kmh, n}] workbook-wide reference
  purposeOptions: [],      // workbook purpose_of_travel values for manual adds
  cur: {person:null, key:null, data:null},
  hidden: new Set(),       // seqs toggled off
  sel: null,               // selected trip seq (detail open)
  modeOpen: false,         // is the selected trip's mode dropdown expanded
  purposeOpen: false,      // is the selected trip's purpose dropdown expanded
  flagSel: null,           // expanded flag id (detail open)
  selId: null,             // selected trip's raw_row (stable id, survives renumber)
  followSel: true,         // keep the selection on its trip across an action
  resolvedOpen: false,     // is the Resolved group expanded
  editsOpen: false,        // is the Edited group expanded
  mergeMode: false,        // are we in trip-merge selection mode?
  mergeSelected: new Set(),// raw_rows (t.id) chosen for merge
  addMode: false,          // are we placing a new (manual_add) trip?
  addPts: [],              // [[lat,lon], ...] clicked while adding (start, then end)
  render: null,            // (fit) => void — assigned by app.js
};
