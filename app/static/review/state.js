// Shared singletons: the Leaflet map + draw layer, the session id, and the one
// mutable state bag every module reads/writes. `render` is a slot app.js fills at
// startup so any module can request a re-render without a circular import.

export const SID = window.SID;

export const map = L.map('map', {zoomControl:true}).setView([49.26,-123.1], 12);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom:19, attribution:'© OpenStreetMap'}).addTo(map);
export const layer = L.layerGroup().addTo(map);

export const S = {
  byPerson: {},
  cur: {person:null, key:null, data:null},
  hidden: new Set(),       // seqs toggled off
  sel: null,               // selected trip seq (detail open)
  resolvedOpen: false,     // is the Resolved group expanded
  render: null,            // (fit) => void — assigned by app.js
};
