// A Markdown renderer for exactly the subset the PRD writer emits.
//
// Vendoring a full parser would be ~50KB for constructs this document never
// uses. What it does use is fixed and small: ATX headings, blockquotes, unordered
// lists, pipe tables, and inline bold/italic/code. Two HTML entities survive on
// purpose — the writer emits `<br>` inside table cells and `&nbsp;` in the
// status line — and they are re-admitted *after* escaping, so nothing else can
// get through.

const INLINE_ALLOWED = [
  [/&lt;br\s*\/?&gt;/g, '<br>'],
  [/&amp;nbsp;/g, '&nbsp;'],
  [/&amp;mdash;/g, '&mdash;'],
];

function escapeHtml(text) {
  return String(text ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

function inline(text) {
  let out = escapeHtml(text);
  for (const [pattern, replacement] of INLINE_ALLOWED) out = out.replace(pattern, replacement);
  // Code first: its contents must not then be read as emphasis. The sentinel is
  // NUL-delimited because a plain positional marker collides with prose, and
  // "within 2 hours" is exactly the kind of sentence this document is made of.
  const codes = [];
  out = out.replace(/`([^`]+)`/g, (_, body) => `\u0000${codes.push(body) - 1}\u0000`);
  out = out
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])_([^_]+)_(?=[\s).,;:]|$)/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return out.replace(/\u0000(\d+)\u0000/g, (_, index) => `<code>${codes[index]}</code>`);
}

function isTableDivider(line) {
  return /^\|?[\s:-]*-[\s|:-]*\|?$/.test(line.trim()) && line.includes('-');
}

function cells(line) {
  return line.replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
}

export function renderMarkdown(source) {
  const lines = String(source ?? '').split('\n');
  const out = [];
  let index = 0;

  const flushList = () => {
    const items = [];
    while (index < lines.length && /^[-*]\s+/.test(lines[index])) {
      items.push(`<li>${inline(lines[index].replace(/^[-*]\s+/, ''))}</li>`);
      index += 1;
    }
    out.push(`<ul>${items.join('')}</ul>`);
  };

  const flushQuote = () => {
    const parts = [];
    while (index < lines.length && lines[index].startsWith('>')) {
      parts.push(inline(lines[index].replace(/^>\s?/, '')));
      index += 1;
    }
    out.push(`<blockquote>${parts.join('<br>')}</blockquote>`);
  };

  const flushTable = () => {
    const header = cells(lines[index]);
    index += 2; // header row + divider
    const body = [];
    while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
      body.push(cells(lines[index]));
      index += 1;
    }
    const head = header.map((c) => `<th>${inline(c)}</th>`).join('');
    const rows = body
      .map((row) => `<tr>${row.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`)
      .join('');
    // Wrapped so a wide traceability matrix scrolls inside itself instead of
    // making the whole page scroll sideways.
    out.push(`<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${rows}</tbody></table></div>`);
  };

  const flushParagraph = () => {
    const parts = [];
    while (index < lines.length && lines[index].trim() && !/^(#{1,6}\s|[-*]\s|>|\|)/.test(lines[index])) {
      parts.push(inline(lines[index]));
      index += 1;
    }
    if (parts.length) out.push(`<p>${parts.join(' ')}</p>`);
  };

  while (index < lines.length) {
    const line = lines[index];

    if (!line.trim()) { index += 1; continue; }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      const level = Math.min(heading[1].length, 6);
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      index += 1;
      continue;
    }

    if (line.startsWith('>')) { flushQuote(); continue; }
    if (/^[-*]\s+/.test(line)) { flushList(); continue; }
    if (line.includes('|') && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
      flushTable();
      continue;
    }
    if (/^(---|___|\*\*\*)\s*$/.test(line)) { out.push('<hr>'); index += 1; continue; }

    flushParagraph();
  }

  return out.join('\n');
}
