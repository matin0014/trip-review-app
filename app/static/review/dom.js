// Cached references to the high-traffic elements. One-off elements (counts,
// crumb, buttons) are queried inline where used.

const $ = id => document.getElementById(id);

export const personSel = $('person'), daySel = $('day');
export const lyTrips = $('lyTrips'), lyStraight = $('lyStraight'), lyFlags = $('lyFlags'), lyEnds = $('lyEnds'), lyPlaces = $('lyPlaces');
export const lyFollow = $('lyFollow');
export const tripsEl = $('trips'), flagsEl = $('flags'), legendEl = $('legend');
export const rFrom = $('rFrom'), rTo = $('rTo');
