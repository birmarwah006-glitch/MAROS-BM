// notes-renderer.js — self-contained markdown + callout renderer, zero CDN deps.
//
// Markdown parsing is done inline (no markdown-it) — covers the subset
// chipper.py actually outputs: headers, bold, italic, inline code, fenced
// code blocks, blockquotes, unordered/ordered lists, links, and paragraphs.
//
// Callouts (> [!TYPE]) are parsed from the raw markdown BEFORE the main
// render pass, so they never get mangled by blockquote handling.
//
// Mermaid diagrams are the one optional external dep — loaded lazily from
// multiple CDN fallbacks. If ALL fail, the diagram renders as a styled
// code block (readable, not broken).
//
// Diagrams render inside an interactive "diagram card" — themed to the
// app palette (reads CSS vars at load), with drag-to-pan, zoom buttons,
// ctrl/cmd+scroll zoom, double-click reset, and a fullscreen toggle.
// Mindmap branches get the same 5-color semantic palette as the callouts.
//
// NEW: content reveals on scroll. #notes-content is an internally-scrolling
// box (student.html sets max-height:500px; overflow-y:auto), so the reveal
// observer is rooted on that container, not the page viewport. Diagram cards
// go further and stagger their nodes/edges in one at a time, so a concept map
// draws itself rather than appearing fully formed.

// ─── Callout preprocessor ────────────────────────────────────────────────────
const CALLOUT_TYPES = ['note', 'tip', 'important', 'warning', 'caution'];
const CALLOUT_LABELS = {
  note: 'DEFINITION', tip: 'EXAMPLE', important: 'KEY INSIGHT',
  warning: 'COMMON MISTAKE', caution: 'CAUTION'
};

function _extractCallouts(md) {
  // Pull > [!TYPE] blocks out of the markdown and replace them with
  // placeholder tokens. Returns { cleaned: string, callouts: string[] }.
  const lines = md.split('\n');
  const out = [];
  const callouts = [];
  let i = 0;
  while (i < lines.length) {
    const m = lines[i].match(/^\s*>\s*\[!(\w+)\]\s*$/i);
    if (m && CALLOUT_TYPES.includes(m[1].toLowerCase())) {
      const type = m[1].toLowerCase();
      const body = [];
      i++;
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        body.push(lines[i].replace(/^\s*>\s?/, ''));
        i++;
      }
      const bodyHtml = _inlineFormat(body.join('\n').trim());
      const label = CALLOUT_LABELS[type] || type.toUpperCase();
      callouts.push(
        `<div class="md-callout md-callout-${type}">` +
        `<div class="md-callout-label">${label}</div>` +
        `<div class="md-callout-body">${bodyHtml}</div></div>`
      );
      out.push(`%%CALLOUT_${callouts.length - 1}%%`);
    } else {
      out.push(lines[i]);
      i++;
    }
  }
  return { cleaned: out.join('\n'), callouts };
}

// ─── Inline formatting ──────────────────────────────────────────────────────
function _esc(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function _inlineFormat(text) {
  let s = _esc(text);
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
  s = s.replace(/\n/g, '<br>');
  return s;
}

// ─── Block-level markdown parser ─────────────────────────────────────────────
function _renderMarkdown(md, callouts) {
  const lines = md.split('\n');
  const html = [];
  let i = 0;
  let inList = false;
  let listType = '';

  function closeList() {
    if (inList) { html.push(listType === 'ol' ? '</ol>' : '</ul>'); inList = false; }
  }

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // Empty line — close any open list, skip
    if (!trimmed) { closeList(); i++; continue; }

    // Callout placeholder
    const calloutMatch = trimmed.match(/^%%CALLOUT_(\d+)%%$/);
    if (calloutMatch) {
      closeList();
      html.push(callouts[parseInt(calloutMatch[1])]);
      i++; continue;
    }

    // Stray concept-map marker (belt & braces — chipper strips these
    // server-side, but never show a raw token to a student)
    if (trimmed === '%%CONCEPT_MAP%%') { i++; continue; }

    // Fenced code block (``` or ```lang)
    if (trimmed.startsWith('```')) {
      closeList();
      const lang = trimmed.slice(3).trim();
      const codeLines = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing ```
      const escaped = _esc(codeLines.join('\n'));
      const langClass = lang ? ` class="language-${lang}"` : '';
      html.push(`<pre><code${langClass}>${escaped}</code></pre>`);
      continue;
    }

    // Headers
    const hMatch = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (hMatch) {
      closeList();
      const level = hMatch[1].length;
      html.push(`<h${level}>${_inlineFormat(hMatch[2])}</h${level}>`);
      i++; continue;
    }

    // Blockquote (non-callout — callouts already extracted)
    if (trimmed.startsWith('>')) {
      closeList();
      const bqLines = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        bqLines.push(lines[i].trim().replace(/^>\s?/, ''));
        i++;
      }
      html.push(`<blockquote>${_inlineFormat(bqLines.join('\n'))}</blockquote>`);
      continue;
    }

    // Unordered list
    if (/^[\-\*]\s+/.test(trimmed)) {
      if (!inList || listType !== 'ul') {
        closeList();
        html.push('<ul>');
        inList = true; listType = 'ul';
      }
      html.push(`<li>${_inlineFormat(trimmed.replace(/^[\-\*]\s+/, ''))}</li>`);
      i++; continue;
    }

    // Ordered list
    if (/^\d+\.\s+/.test(trimmed)) {
      if (!inList || listType !== 'ol') {
        closeList();
        html.push('<ol>');
        inList = true; listType = 'ol';
      }
      html.push(`<li>${_inlineFormat(trimmed.replace(/^\d+\.\s+/, ''))}</li>`);
      i++; continue;
    }

    // Horizontal rule
    if (/^-{3,}$/.test(trimmed) || /^\*{3,}$/.test(trimmed)) {
      closeList();
      html.push('<hr>');
      i++; continue;
    }

    // Regular paragraph — collect consecutive non-special lines
    closeList();
    const paraLines = [];
    while (i < lines.length && lines[i].trim() &&
           !lines[i].trim().startsWith('#') &&
           !lines[i].trim().startsWith('```') &&
           !lines[i].trim().startsWith('>') &&
           !lines[i].trim().match(/^%%CALLOUT_\d+%%$/) &&
           lines[i].trim() !== '%%CONCEPT_MAP%%' &&
           !lines[i].trim().match(/^[\-\*]\s+/) &&
           !lines[i].trim().match(/^\d+\.\s+/)) {
      paraLines.push(lines[i].trim());
      i++;
    }
    if (paraLines.length) {
      html.push(`<p>${_inlineFormat(paraLines.join(' '))}</p>`);
    }
  }
  closeList();
  return html.join('\n');
}

// ─── Theme palette — read live CSS vars so diagrams match the app skin ──────
const _FALLBACK = {
  fg: '#e6edf3', surface: '#161b22', border: '#30363d', green: '#57AB5A',
  blue: '#539BF5', purple: '#986EE2', amber: '#C69026', red: '#E5534B',
};

function _cssVar(name, fallback) {
  const v = getComputedStyle(document.body).getPropertyValue(name).trim();
  return v || fallback;
}

function _palette() {
  const light = document.body.classList.contains('light');
  return {
    light,
    fg:      _cssVar('--fg', light ? '#1f2328' : _FALLBACK.fg),
    surface: _cssVar('--surface', light ? '#f6f8fa' : _FALLBACK.surface),
    border:  _cssVar('--border', light ? '#d0d7de' : _FALLBACK.border),
    green:   _cssVar('--green', _FALLBACK.green),
    blue:    _FALLBACK.blue,
    purple:  _FALLBACK.purple,
    amber:   _FALLBACK.amber,
    red:     _FALLBACK.red,
  };
}

// ─── Mermaid loader (lazy, multi-CDN fallback, themed) ──────────────────────
let _mermaid = null;
let _mermaidFailed = false;

const MERMAID_CDNS = [
  'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs',
  'https://unpkg.com/mermaid@11/dist/mermaid.esm.min.mjs',
];

async function _loadMermaid() {
  if (_mermaid) return _mermaid;
  if (_mermaidFailed) return null;

  for (const url of MERMAID_CDNS) {
    try {
      const mod = await import(url);
      _mermaid = mod.default || mod;
      const p = _palette();
      _mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        suppressErrorRendering: true,
        theme: 'base',
        themeVariables: {
          fontFamily: 'var(--font-mono), monospace',
          fontSize: '13px',
          // Nodes
          primaryColor: p.light ? 'rgba(87,171,90,0.10)' : 'rgba(87,171,90,0.14)',
          primaryTextColor: p.fg,
          primaryBorderColor: p.green,
          // Edges + labels
          lineColor: p.light ? '#8b949e' : '#6e7681',
          edgeLabelBackground: p.surface,
          // Secondary/tertiary node tints (subgraph internals etc.)
          secondaryColor: p.light ? 'rgba(83,155,245,0.10)' : 'rgba(83,155,245,0.14)',
          secondaryBorderColor: p.blue,
          tertiaryColor: p.light ? 'rgba(152,110,226,0.10)' : 'rgba(152,110,226,0.14)',
          tertiaryBorderColor: p.purple,
          // Subgraph (cluster) framing
          clusterBkg: p.light ? 'rgba(0,0,0,0.03)' : 'rgba(255,255,255,0.03)',
          clusterBorder: p.border,
          titleColor: p.fg,
          // Mindmap branch colors — same semantic palette as the callouts,
          // so each branch of a mindmap gets its own accent color. Timeline
          // sections and state/sequence actors reuse the same cScale ramp.
          'cScale0': p.green,
          'cScale1': p.blue,
          'cScale2': p.purple,
          'cScale3': p.amber,
          'cScale4': p.red,
          'cScale5': p.green,
          'cScaleLabel0': p.fg, 'cScaleLabel1': p.fg, 'cScaleLabel2': p.fg,
          'cScaleLabel3': p.fg, 'cScaleLabel4': p.fg, 'cScaleLabel5': p.fg,
        },
        mindmap: { padding: 14, maxNodeWidth: 200 },
        flowchart: { curve: 'basis', padding: 12, nodeSpacing: 40, rankSpacing: 50 },
      });
      console.log('[notes-renderer] mermaid loaded from', url);
      return _mermaid;
    } catch (err) {
      console.warn('[notes-renderer] mermaid failed from', url, err);
    }
  }
  _mermaidFailed = true;
  console.warn('[notes-renderer] all mermaid CDNs failed — diagrams will show as code');
  return null;
}

// ─── Interactive diagram card: pan / zoom / fullscreen ──────────────────────
const _ZOOM_MIN = 0.4, _ZOOM_MAX = 3, _ZOOM_STEP = 1.2;

function _diagramKind(src) {
  // Badge label per Mermaid shape. chipper.py can now emit seven shapes, so
  // "CONCEPT MAP" is the fallback for flowchart/graph rather than the default
  // for everything that isn't a mindmap.
  const first = src.trim().split('\n')[0].trim();
  if (first.startsWith('mindmap'))         return 'MINDMAP';
  if (first.startsWith('timeline'))        return 'TIMELINE';
  if (first.startsWith('stateDiagram'))    return 'STATE DIAGRAM';
  if (first.startsWith('sequenceDiagram')) return 'SEQUENCE';
  if (first.startsWith('erDiagram'))       return 'RELATIONSHIPS';
  return 'CONCEPT MAP';
}

function _buildDiagramCard(svgHtml, kind) {
  const card = document.createElement('div');
  card.className = 'mermaid-card';
  card.innerHTML = `
    <div class="mermaid-card-head">
      <span class="mermaid-card-badge">${kind}</span>
      <span class="mermaid-card-hint">drag to pan · ctrl+scroll to zoom</span>
      <span class="mermaid-card-tools">
        <button class="mm-btn" data-act="out"  title="Zoom out">&minus;</button>
        <button class="mm-btn" data-act="in"   title="Zoom in">+</button>
        <button class="mm-btn" data-act="reset" title="Reset view">&#8634;</button>
        <button class="mm-btn" data-act="fs"   title="Fullscreen">&#x26F6;</button>
      </span>
    </div>
    <div class="mermaid-viewport"><div class="mermaid-stage"></div></div>
  `;
  const viewport = card.querySelector('.mermaid-viewport');
  const stage = card.querySelector('.mermaid-stage');
  stage.innerHTML = svgHtml;

  // ── view state ──
  let scale = 1, tx = 0, ty = 0;
  const apply = () => {
    stage.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
  };
  const reset = () => { scale = 1; tx = 0; ty = 0; apply(); };

  const zoom = (factor) => {
    scale = Math.min(_ZOOM_MAX, Math.max(_ZOOM_MIN, scale * factor));
    apply();
  };

  // ── toolbar ──
  card.querySelector('.mermaid-card-tools').addEventListener('click', (e) => {
    const act = e.target.closest('.mm-btn')?.dataset.act;
    if (!act) return;
    if (act === 'in') zoom(_ZOOM_STEP);
    else if (act === 'out') zoom(1 / _ZOOM_STEP);
    else if (act === 'reset') reset();
    else if (act === 'fs') {
      if (document.fullscreenElement === card) document.exitFullscreen();
      else card.requestFullscreen?.();
    }
  });
  card.addEventListener('fullscreenchange', () => {
    card.classList.toggle('mermaid-card-fs', document.fullscreenElement === card);
  });
  document.addEventListener('fullscreenchange', () => {
    card.classList.toggle('mermaid-card-fs', document.fullscreenElement === card);
  });

  // ── drag to pan ──
  let dragging = false, sx = 0, sy = 0, stx = 0, sty = 0;
  viewport.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    dragging = true;
    sx = e.clientX; sy = e.clientY; stx = tx; sty = ty;
    viewport.setPointerCapture(e.pointerId);
    viewport.classList.add('dragging');
  });
  viewport.addEventListener('pointermove', (e) => {
    if (!dragging) return;
    tx = stx + (e.clientX - sx);
    ty = sty + (e.clientY - sy);
    apply();
  });
  const endDrag = (e) => {
    dragging = false;
    viewport.classList.remove('dragging');
    if (e.pointerId != null) { try { viewport.releasePointerCapture(e.pointerId); } catch (_) {} }
  };
  viewport.addEventListener('pointerup', endDrag);
  viewport.addEventListener('pointercancel', endDrag);

  // ── ctrl/cmd + scroll to zoom (plain scroll still scrolls the page) ──
  viewport.addEventListener('wheel', (e) => {
    if (!e.ctrlKey && !e.metaKey) return;
    e.preventDefault();
    zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1);
  }, { passive: false });

  // ── double-click resets ──
  viewport.addEventListener('dblclick', reset);

  return card;
}

async function _renderMermaidBlocks(container) {
  const codeBlocks = container.querySelectorAll('pre > code.language-mermaid');
  if (!codeBlocks.length) return;

  const mermaid = await _loadMermaid();
  if (!mermaid) return;  // all CDNs failed — leave as code blocks

  for (let i = 0; i < codeBlocks.length; i++) {
    const codeEl = codeBlocks[i];
    const pre = codeEl.parentElement;
    const src = codeEl.textContent;
    try {
      // parse() throws cleanly on bad syntax — no watermark SVG ever gets created
      await mermaid.parse(src);
      const renderId = `mermaid-${Date.now()}-${i}`;
      const { svg } = await mermaid.render(renderId, src);
      const card = _buildDiagramCard(svg, _diagramKind(src));
      pre.replaceWith(card);
    } catch (err) {
      console.warn('[notes-renderer] mermaid render failed for block', i, err);
      pre.classList.add('mermaid-error');
    }
  }
}

// ─── Scroll reveal ──────────────────────────────────────────────────────────
// Respect the OS reduced-motion setting: skip all staggering and just show
// everything. Cheap to honour, and animation here is decoration, not meaning.
function _prefersReducedMotion() {
  return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;
}

function _animateDiagramNodes(card) {
  // Stagger the rendered SVG's nodes and edges so a diagram assembles itself
  // instead of appearing whole. Mermaid's internal class names vary a little
  // between diagram types (and between minor versions), so this queries several
  // and degrades to "nothing animates, diagram still visible" if none match —
  // it never hides content it can't animate back in.
  const svg = card.querySelector('.mermaid-stage svg');
  if (!svg) return;

  const nodes = svg.querySelectorAll(
    '.node, .mindmap-node, g.cluster, .statediagram-state, .actor, .er.entityBox, .timeline-node'
  );
  const edges = svg.querySelectorAll(
    '.edgePath, .edge, path.flowchart-link, .messageLine0, .messageLine1, .relationshipLine'
  );
  if (!nodes.length && !edges.length) return;

  [...nodes, ...edges].forEach(el => { el.style.opacity = '0'; });

  nodes.forEach((el, i) => {
    setTimeout(() => {
      el.style.transition = 'opacity 0.35s ease';
      el.style.opacity = '1';
    }, i * 110);
  });
  edges.forEach((el, i) => {
    setTimeout(() => {
      el.style.transition = 'opacity 0.35s ease';
      el.style.opacity = '1';
    }, i * 110 + 60);
  });
}

function _initScrollReveal(container) {
  const targets = container.querySelectorAll(
    'h1, h2, h3, h4, p, blockquote, pre, ul, ol, .md-callout, .mermaid-card'
  );
  if (!targets.length) return;

  if (_prefersReducedMotion() || typeof IntersectionObserver === 'undefined') {
    return;  // leave everything visible, no classes added
  }

  targets.forEach(el => el.classList.add('reveal-hidden'));

  // root MUST be the notes container: student.html renders notes into
  // #notes-content, which has its own max-height + overflow-y:auto. Using the
  // default viewport root would mark everything intersecting on open and the
  // reveal would never fire on actual scroll.
  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add('reveal-visible');
      if (entry.target.classList.contains('mermaid-card')) {
        _animateDiagramNodes(entry.target);
      }
      observer.unobserve(entry.target);   // animate once, not on every scroll pass
    });
  }, {
    root: container,
    threshold: 0.1,
    rootMargin: '0px 0px -30px 0px',
  });

  targets.forEach(el => observer.observe(el));
}

// ─── Public API ──────────────────────────────────────────────────────────────
export async function renderNotes(markdownStr, targetEl) {
  if (!targetEl) return;
  targetEl.innerHTML = '';

  if (!markdownStr || !markdownStr.trim()) {
    targetEl.innerHTML = '<div style="color:var(--muted2); font-family:var(--font-mono); font-size:12px">No notes yet.</div>';
    return;
  }

  // 1. Extract callouts from raw markdown (before any other parsing)
  const { cleaned, callouts } = _extractCallouts(markdownStr);

  // 2. Render markdown → HTML (self-contained, no CDN)
  const html = _renderMarkdown(cleaned, callouts);
  targetEl.innerHTML = html;

  // 3. Render mermaid diagrams (lazy CDN load, graceful fallback)
  await _renderMermaidBlocks(targetEl);

  // 4. Reveal content as the student scrolls the notes panel. Runs LAST so the
  //    diagram cards already exist in the DOM and get observed alongside text.
  _initScrollReveal(targetEl);
}

// ─── CSS — injected once at import time (synchronous, no fetch) ─────────────
function _injectStyles() {
  if (document.getElementById('notes-renderer-styles')) return;
  const style = document.createElement('style');
  style.id = 'notes-renderer-styles';
  style.textContent = `
    /* ── Callouts ───────────────────────────────────────────────────── */
    .md-callout {
      padding: 12px 16px;
      margin: 14px 0;
      border-left: 3px solid var(--callout-border, var(--border));
      background: var(--callout-bg, var(--surface));
      border-radius: 4px;
      font-size: 13px;
      line-height: 1.7;
    }
    .md-callout-label {
      font-family: var(--font-mono);
      font-size: 10px;
      letter-spacing: 0.08em;
      color: var(--callout-border, var(--muted2));
      margin-bottom: 4px;
      font-weight: 600;
    }
    .md-callout-body { color: var(--fg); }
    .md-callout-body code { background: rgba(0,0,0,0.15); padding: 1px 5px; border-radius: 3px; }

    /* Semantic colors */
    .md-callout-note      { --callout-border: #539BF5; --callout-bg: rgba(83,155,245,0.06); }
    .md-callout-tip       { --callout-border: #57AB5A; --callout-bg: rgba(87,171,90,0.06); }
    .md-callout-important { --callout-border: #986EE2; --callout-bg: rgba(152,110,226,0.06); }
    .md-callout-warning   { --callout-border: #C69026; --callout-bg: rgba(198,144,38,0.06); }
    .md-callout-caution   { --callout-border: #E5534B; --callout-bg: rgba(229,83,75,0.06); }

    /* ── Interactive diagram card ─────────────────────────────────── */
    /* No mount animation here — the scroll-reveal observer owns the entrance,
       so the card fades in when it actually scrolls into view instead of the
       moment it's built (which fired while still offscreen, and double-animated
       once reveal was added). */
    .mermaid-card {
      border-radius: var(--radius);
      margin: 18px 0;
      overflow: hidden;
      background:
        linear-gradient(var(--surface), var(--surface)) padding-box,
        linear-gradient(120deg, #57AB5A, #539BF5, #986EE2) border-box;
      border: 1px solid transparent;
      transition: box-shadow 0.25s ease;
    }
    .mermaid-card:hover {
      box-shadow: 0 4px 24px rgba(87,171,90,0.10), 0 2px 8px rgba(0,0,0,0.18);
    }

    .mermaid-card-head {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px 12px;
      border-bottom: 1px solid var(--border);
      background: rgba(0,0,0,0.10);
    }
    body.light .mermaid-card-head { background: rgba(0,0,0,0.03); }

    .mermaid-card-badge {
      font-family: var(--font-mono);
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.1em;
      color: var(--green);
      padding: 2px 8px;
      border: 1px solid var(--green);
      border-radius: 999px;
      background: rgba(87,171,90,0.08);
      white-space: nowrap;
    }
    .mermaid-card-hint {
      font-family: var(--font-mono);
      font-size: 10px;
      color: var(--muted2);
      flex: 1;
      text-align: left;
      opacity: 0;
      transition: opacity 0.2s ease;
      user-select: none;
    }
    .mermaid-card:hover .mermaid-card-hint { opacity: 0.8; }

    .mermaid-card-tools { display: flex; gap: 4px; }
    .mm-btn {
      width: 24px; height: 24px;
      display: inline-flex; align-items: center; justify-content: center;
      font-family: var(--font-mono); font-size: 13px; line-height: 1;
      color: var(--muted2);
      background: transparent;
      border: 1px solid var(--border);
      border-radius: 5px;
      cursor: pointer;
      transition: color 0.15s, border-color 0.15s, background 0.15s;
      padding: 0;
    }
    .mm-btn:hover { color: var(--green); border-color: var(--green); background: rgba(87,171,90,0.08); }
    .mm-btn:active { transform: scale(0.92); }
    .mm-btn:focus-visible { outline: 2px solid var(--green); outline-offset: 2px; }

    .mermaid-viewport {
      position: relative;
      overflow: hidden;
      min-height: 180px;
      max-height: 460px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: grab;
      touch-action: none;
      padding: 16px;
    }
    .mermaid-viewport.dragging { cursor: grabbing; }
    .mermaid-card-fs .mermaid-viewport { max-height: none; height: calc(100vh - 42px); background: var(--surface); }

    .mermaid-stage {
      transition: transform 0.08s linear;
      will-change: transform;
    }
    .mermaid-stage svg { max-width: 100%; height: auto; display: block; }

    .mermaid-error {
      border: 1px solid var(--border);
      background: var(--surface);
      padding: 10px 14px;
      border-radius: 4px;
    }

    /* ── Notes typography (.notes-md parent) ──────────────────────── */
    .notes-md { font-size: 13px; line-height: 1.7; color: var(--muted2); }
    .notes-md h1 { font-size: 18px; color: var(--fg); margin: 22px 0 12px; font-weight: 600; }
    .notes-md h2 { font-size: 16px; color: var(--fg); margin: 20px 0 10px; font-weight: 600; }
    .notes-md h3 { font-size: 14px; color: var(--fg); margin: 16px 0 8px; font-weight: 600; }
    .notes-md p  { margin: 8px 0; color: var(--fg); }
    .notes-md strong { color: var(--fg); }
    .notes-md em { font-style: italic; }
    .notes-md code {
      font-family: var(--font-mono); font-size: 12px;
      background: var(--surface); padding: 1px 5px; border-radius: 3px; color: var(--green);
    }
    .notes-md pre {
      background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
      padding: 14px 16px; overflow-x: auto; margin: 12px 0;
    }
    .notes-md pre code { background: none; color: var(--fg); padding: 0; }
    .notes-md ul, .notes-md ol { padding-left: 22px; margin: 8px 0; }
    .notes-md li { margin: 4px 0; color: var(--fg); }
    .notes-md blockquote {
      border-left: 2px solid var(--border);
      padding-left: 14px;
      margin: 12px 0;
      color: var(--muted2);
    }
    .notes-md hr {
      border: none;
      border-top: 1px solid var(--border);
      margin: 16px 0;
    }
    .notes-md a { color: var(--green); text-decoration: none; }
    .notes-md a:hover { text-decoration: underline; }

    /* ── Scroll reveal ────────────────────────────────────────────── */
    .reveal-hidden  { opacity: 0; transform: translateY(14px); }
    .reveal-visible {
      opacity: 1;
      transform: translateY(0);
      transition: opacity 0.5s ease, transform 0.5s ease;
    }

    @media (prefers-reduced-motion: reduce) {
      .reveal-hidden, .reveal-visible {
        opacity: 1;
        transform: none;
        transition: none;
      }
      .mermaid-stage { transition: none; }
    }
  `;
  document.head.appendChild(style);
}
_injectStyles();