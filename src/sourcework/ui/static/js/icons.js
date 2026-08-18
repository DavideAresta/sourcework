// Line icons, drawn inline.
//
// One path table and two renderers, so an icon is a string this app already
// knows how to put in the DOM rather than a file it has to fetch. No sprite, no
// icon font, no external request - which is also what lets the architecture
// diagram nest them inside its own SVG.
//
// `currentColor` throughout: a node sets `style="color:…"` once on its group and
// the icon, the title and the accent bar all follow. That is what makes the
// running/done/failed states one CSS rule each instead of three redraws.
//
// Keyed by agent name, exactly as `cli.py:AGENTS` spells them, so the diagram
// and the railway look an icon up by the same string the mesh reports.

const PATHS = {
  // The run itself
  orchestrator:
    '<path d="M12 3v4M12 17v4M3 12h4M17 12h4"/><circle cx="12" cy="12" r="4"/>'
    + '<path d="M5.6 5.6l2.9 2.9M15.5 15.5l2.9 2.9M18.4 5.6l-2.9 2.9M8.5 15.5l-2.9 2.9"/>',
  // Reading the world
  ingestion: '<path d="M4 5h10l6 6v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"/>'
    + '<path d="M14 5v6h6M8 14h7M8 17h5"/>',
  vision: '<path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6-10-6-10-6z"/><circle cx="12" cy="12" r="2.6"/>',
  transcript: '<path d="M12 4a3 3 0 0 1 3 3v4a3 3 0 0 1-6 0V7a3 3 0 0 1 3-3z"/>'
    + '<path d="M5 11a7 7 0 0 0 14 0M12 18v3M9 21h6"/>',
  confluence: '<path d="M3 16.5c3-5 6-6.5 9-4.5s6 .5 9-4.5"/><path d="M3 16.5l3.5 3M21 7.5L17.5 4"/>',
  // Making the document
  requirements: '<path d="M9 4h9a1 1 0 0 1 1 1v15a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V8z"/>'
    + '<path d="M9 4v4H5M8.5 13l2 2 4-4"/>',
  writer: '<path d="M4 20h4l10-10a2.1 2.1 0 0 0-3-3L5 17v3z"/><path d="M13.5 6.5l3 3"/>',
  critic: '<circle cx="11" cy="11" r="6.5"/><path d="M15.8 15.8L21 21"/><path d="M8.6 11h4.8"/>',
  // Generic
  doc: '<path d="M6 3h8l4 4v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/>'
    + '<path d="M14 3v4h4M8 12h8M8 16h6"/>',
  flag: '<path d="M5 21V4M5 4h11l-2 3.5L16 11H5"/>',
  publish: '<path d="M12 19V6M6 12l6-6 6 6"/><path d="M4 21h16"/>',
};

const OPEN = '<svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
  + 'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"';

export function has(id) {
  return Object.prototype.hasOwnProperty.call(PATHS, id);
}

// An unknown id still draws something, and draws it in the same weight - a
// missing icon should look like a plain node, never like a broken one.
function body(id) {
  return PATHS[id] ?? '<circle cx="12" cy="12" r="4"/>';
}

export function svg(id) {
  return `${OPEN}>${body(id)}</svg>`;
}

// Positioned for nesting inside another SVG (the architecture canvas), where a
// bare <svg> would stretch to the whole viewport instead of sitting where the
// node put it.
export function nested(id, x, y, size) {
  return `${OPEN} x="${x}" y="${y}" width="${size}" height="${size}">${body(id)}</svg>`;
}
