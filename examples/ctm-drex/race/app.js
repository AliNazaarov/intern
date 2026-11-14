// CTM Race front end. Vanilla JS, no build step. All API text is rendered with textContent.
// Dev helpers: ?mock=1 fakes the API, &auto=1 starts a race on load, &open=N opens claim N's drawer,
// &fail=drex|rag|frontier makes that mock lane fail.
(() => {
  'use strict';

  const params = new URLSearchParams(location.search);
  const MOCK = params.get('mock') === '1';
  const NONE = '·'; // placeholder for a value that is not known yet
  const $ = (id) => document.getElementById(id);

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }

  // ---------- formatting ----------
  const isNum = (x) => typeof x === 'number' && Number.isFinite(x);

  function fmtUsd(x) {
    if (!isNum(x)) return NONE;
    if (x === 0) return '$0.00';
    const a = Math.abs(x);
    if (a >= 1) return '$' + x.toFixed(2);
    // three significant digits, never exponent notation, at least two decimals
    const decimals = Math.min(12, Math.max(2, 2 - Math.floor(Math.log10(a))));
    let s = x.toFixed(decimals);
    s = s.replace(/0+$/, '');
    const have = s.length - s.indexOf('.') - 1;
    if (have < 2) s += '0'.repeat(2 - have);
    return '$' + s;
  }
  function fmtSec(x) {
    if (!isNum(x)) return NONE;
    return x >= 100 ? x.toFixed(0) : x >= 10 ? x.toFixed(1) : x.toFixed(2);
  }
  function fmtRatio(r) { return (r >= 10 ? Math.round(r).toLocaleString('en-US') : r.toFixed(1)) + '×'; }
  function fmtInt(x) { return isNum(x) ? Math.round(x).toLocaleString('en-US') : NONE; }

  const VERDICT_LABEL = { supported: 'Proven', contradicted: 'Contradicted', unsupported: 'Client-stated only', error: 'Error' };
  function chip(verdict, fresh) {
    const v = Object.prototype.hasOwnProperty.call(VERDICT_LABEL, verdict) ? verdict : 'error';
    return el('span', 'chip ' + v + (fresh ? ' fresh' : ''), VERDICT_LABEL[v]);
  }
  function mark(correct, fresh) {
    const m = el('span', 'mark' + (fresh ? ' fresh' : ''));
    if (correct === true) { m.classList.add('yes'); m.textContent = '✓'; m.title = 'Matches the hand label'; }
    else if (correct === false) { m.classList.add('no'); m.textContent = '✗'; m.title = 'Differs from the hand label'; }
    return m;
  }

  // ---------- API (real) ----------
  async function readJson(res) {
    let body = null;
    try { body = await res.json(); } catch (_) { /* not JSON */ }
    if (!res.ok) throw new Error((body && body.error) ? String(body.error) : 'HTTP ' + res.status);
    if (body === null) throw new Error('The server returned no JSON');
    return body;
  }
  const realApi = {
    cases: () => fetch('/api/cases').then(readJson),
    check: (caseId, engine) => fetch('/api/check', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ case: caseId, engine }),
    }).then(readJson),
    exhibit: (caseId, name) => fetch('/api/exhibit?case=' + encodeURIComponent(caseId) + '&name=' + encodeURIComponent(name)).then(readJson),
    upload: (form) => fetch('/api/upload', { method: 'POST', body: form }).then(readJson),
  };

  // ---------- API (mock, ?mock=1) ----------
  const mockApi = (() => {
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    const EX = {
      'E1-cv.md': 'Curriculum vitae: Dr. Ilse Marwick. PhD, Computer Science, Calder University, 2017. Thesis: "Measuring what models claim". Chief Scientist, Lumen Audit Labs, 2021 to present.',
      'E2-press-article.md': 'The Bay Ledger, March 14, 2024. "The auditor of the machines." Award-winning researcher Ilse Marwick now runs a team of dozens at Lumen Audit Labs, which closed a multi-million round and counts dozens of enterprises as customers.',
      'E3-award-certificate.md': 'Pacific AI Builders Forum 2024. This certifies that Dr. Ilse Marwick was a FINALIST for Applied AI Innovator of the Year. The 2024 award was presented to Dr. Tobias Renn.',
      'E5-org-chart.md': 'Lumen Audit Labs organisation chart, January 2024. Chief Scientist: I. Marwick (reports to the Board). Research and engineering: 6 people in total, including the Chief Scientist.',
      'E6-term-sheet.md': 'Summary of terms. Lumen Audit Labs, Inc. Seed financing of $4,200,000 led by Northgate Ventures. Closing date: May 2, 2023.',
      'E7-publication-list.md': 'Publications of I. Marwick: 9 peer-reviewed papers (2016 to 2024). Total citations per the attached index export: 2,148.',
      'E8-membership-letter.md': 'Association for Computational Evaluation. February 12, 2024. We confirm the elevation of Dr. Marwick to Senior Member, a grade that requires nomination by two Fellows.',
      'E9-reviewer-invitation.md': 'Journal of Model Assurance. We thank Dr. Marwick for completing four manuscript reviews during 2023.',
      'E10-offer-letter.md': 'Lumen Audit Labs offer letter. Position: Chief Scientist. Annual base salary: $210,000. Equity per the attached grant.',
      'E11-customer-letter.md': 'Letter from the VP Operations: as of December 2023 the audit framework is deployed at 14 enterprise customers.',
      'E12-patent-filing.md': 'USPTO filing receipt. Title: Systems for auditing model outputs. Inventors: Marwick, Ilse; Okafor, Daniel.',
    };
    const P = 'E2-press-article.md';
    // [claim, hand label, exhibit, note, top-3 exhibits RAG retrieves, overrides {rag, frontier, drex}]
    const ROWS = [
      ['In 2024, Dr. Marwick was named the winner of the Applied AI Innovator of the Year award at the Pacific AI Builders Forum.', 'contradicted', 'E3-award-certificate.md', 'Certificate says Finalist; the award went to another person.', [P, 'E1-cv.md', 'E8-membership-letter.md'], { rag: 'supported' }],
      ['She also received the Golden Gate Founders Medal in 2022 for her early work on model auditing.', 'unsupported', null, 'No exhibit mentions this medal.', [P, 'E1-cv.md', 'E3-award-certificate.md'], {}],
      ['In February 2024, Dr. Marwick was elevated to Senior Member of the Association for Computational Evaluation, a grade that requires nomination by two Fellows.', 'supported', 'E8-membership-letter.md', 'Letter dated Feb 12, 2024 confirms the elevation.', ['E8-membership-letter.md', 'E1-cv.md', P], {}],
      ['Dr. Marwick leads a team of dozens of engineers at Lumen Audit Labs.', 'contradicted', 'E5-org-chart.md', 'Org chart lists six people in total.', [P, 'E1-cv.md', 'E10-offer-letter.md'], { rag: 'supported' }],
      ['Her work was profiled in The Bay Ledger in March 2024.', 'supported', P, 'Article dated March 14, 2024.', [P, 'E1-cv.md', 'E7-publication-list.md'], {}],
      ['Lumen Audit Labs raised a $12 million Series A led by Northgate Ventures.', 'contradicted', 'E6-term-sheet.md', 'Term sheet shows a $4.2M seed round.', [P, 'E11-customer-letter.md', 'E10-offer-letter.md'], { rag: 'supported' }],
      ['She has authored 14 peer-reviewed papers on model evaluation.', 'contradicted', 'E7-publication-list.md', 'Publication list shows 9 papers.', ['E7-publication-list.md', 'E1-cv.md', 'E9-reviewer-invitation.md'], {}],
      ['Her papers have been cited more than 2,100 times.', 'supported', 'E7-publication-list.md', 'Index export shows 2,148 citations.', ['E7-publication-list.md', P, 'E1-cv.md'], {}],
      ['She served as a reviewer for the Journal of Model Assurance in 2023.', 'supported', 'E9-reviewer-invitation.md', 'Four reviews completed in 2023.', ['E9-reviewer-invitation.md', 'E7-publication-list.md', 'E1-cv.md'], {}],
      ['She judged the finals of the 2023 West Coast ML Hackathon.', 'unsupported', null, 'No exhibit mentions judging this event.', ['E9-reviewer-invitation.md', P, 'E3-award-certificate.md'], { frontier: 'supported' }],
      ['Her base salary is $310,000, well above the 90th percentile for her occupation.', 'contradicted', 'E10-offer-letter.md', 'Offer letter states $210,000.', ['E10-offer-letter.md', 'E1-cv.md', 'E6-term-sheet.md'], {}],
      ['Dr. Marwick holds a PhD in Computer Science from Calder University, awarded in 2017.', 'supported', 'E1-cv.md', 'CV lists the degree and year.', ['E1-cv.md', 'E7-publication-list.md', P], {}],
      ['Her audit framework is used by more than 40 enterprise customers.', 'contradicted', 'E11-customer-letter.md', 'Customer letter says 14 customers.', [P, 'E12-patent-filing.md', 'E1-cv.md'], { rag: 'supported' }],
      ['She is a named inventor on the patent application "Systems for auditing model outputs".', 'supported', 'E12-patent-filing.md', 'Filing receipt lists her first.', ['E12-patent-filing.md', 'E1-cv.md', 'E7-publication-list.md'], {}],
      ['She serves as Chief Scientist, a critical role reporting directly to the board.', 'supported', 'E5-org-chart.md', 'Org chart shows the reporting line.', ['E1-cv.md', 'E10-offer-letter.md', 'E5-org-chart.md'], { drex: 'unsupported' }],
      ['She delivered the opening keynote at the 2024 Pacific AI Builders Forum.', 'unsupported', null, 'The certificate does not mention a keynote.', ['E3-award-certificate.md', P, 'E8-membership-letter.md'], {}],
    ];
    const CASES = {
      case01: { rows: ROWS, labelled: true },
      case02: { rows: ROWS.slice(3, 15), labelled: true },
    };
    const BASELINES = { 'or-gpt-5.6-sol': 'GPT-5.6 Sol', 'di-gemini-3.1-pro': 'Gemini 3.1 Pro', 'or-grok-4.7': 'Grok 4.7' };
    const PROFILE = {
      drex: { ms: 410, cost: 0.00031, tokens: 10234, requests: 2, label: 'Drex (drex-latest)' },
      rag: { ms: 1520, cost: 0.0021, tokens: 9810, requests: 17, label: 'RAG (text-embedding-3-small + judge)' },
      frontier: { ms: 2630, cost: 0.0684, tokens: 11872, requests: 1, label: null },
    };
    const listCase = (id) => ({ id, claims: CASES[id].rows.length, exhibits: Object.keys(EX), claim_texts: CASES[id].rows.map((r) => r[0]) });
    return {
      async cases() { await sleep(120); return { cases: Object.keys(CASES).map(listCase), baselines: BASELINES }; },
      async check(caseId, engine) {
        const kind = engine === 'drex' || engine === 'rag' ? engine : 'frontier';
        const p = PROFILE[kind];
        const ms = p.ms * (0.94 + Math.random() * 0.12);
        await sleep(ms);
        if (params.get('fail') === kind) throw new Error('RateLimitError: 429 from provider (mock)');
        const c = CASES[caseId];
        if (!c) throw new Error('unknown case or engine');
        const verdicts = c.rows.map(([claim, label, exhibit, note, retrieved, over]) => {
          const verdict = over[kind] || label;
          const expected = c.labelled ? label : null;
          const v = {
            claim, verdict, expected, note: verdict === label ? note : '',
            source: verdict === 'unsupported' ? null : (kind === 'rag' ? retrieved[0] : (exhibit || retrieved[0])),
            correct: expected === null ? null : verdict === expected,
          };
          if (kind === 'rag') v.retrieved = retrieved.map((name, i) => ({ exhibit: name, text: EX[name], score: Math.round((0.84 - i * 0.07) * 100) / 100 }));
          return v;
        });
        const scale = c.rows.length / 16;
        return {
          engine: p.label || (BASELINES[engine] + ' (' + engine + ')'), seconds: Math.round(ms) / 1000,
          cost_usd: p.cost * scale, input_tokens: Math.round(p.tokens * scale),
          requests: kind === 'rag' ? c.rows.length + 1 : p.requests,
          correct: verdicts.filter((v) => v.correct === true).length, claims: verdicts.length, verdicts,
        };
      },
      async exhibit(caseId, name) {
        await sleep(150);
        if (!EX[name]) throw new Error('exhibit not found');
        return { name, text: '# ' + name.replace(/\.md$/, '') + '\n\n' + EX[name] };
      },
      async upload() {
        await sleep(3200);
        const id = 'upload-' + Math.random().toString(16).slice(2, 6);
        CASES[id] = { rows: ROWS.slice(0, 12), labelled: false };
        const c = listCase(id);
        return { case: id, claims: c.claims, exhibits: c.exhibits, parse_seconds: 3.2, parse_cost_usd: 0.02 };
      },
    };
  })();

  const api = MOCK ? mockApi : realApi;

  // ---------- state ----------
  const LANES = [
    { id: 'drex', name: 'Drex', hint: 'Nace decision model · all exhibits held as one state', short: 'Drex' },
    { id: 'rag', name: 'Vector search (RAG)', hint: 'chunks + embeddings + top-3 per claim + judge LLM', short: 'RAG' },
    { id: 'frontier', name: 'Frontier model', hint: 'reads the whole case', short: 'Frontier' },
  ];
  const COLORS = { drex: '#6f9c37', rag: '#4677ad', frontier: '#e8c1b0' };
  const state = {
    cases: [], baselines: {}, running: false, caseId: null, finishCount: 0,
    selected: null, claimTexts: {}, parseInfo: {}, exhibitCache: new Map(), t0: 0,
  };
  const lanes = LANES.map((def) => ({ def, status: 'idle', result: null, error: null, wall: null, rank: null, costAnim: null, engineUsed: null, dom: null, rows: [] }));
  const laneById = (id) => lanes.find((l) => l.def.id === id);

  function engineKey(lane) { return lane.def.id === 'frontier' ? $('opponent').value : lane.def.id; }
  function laneName(lane) {
    if (lane.def.id !== 'frontier') return lane.def.name;
    const key = lane.engineUsed || $('opponent').value;
    return state.baselines[key] ? String(state.baselines[key]) : lane.def.name;
  }
  function setStatus(text, isErr) {
    const s = $('status');
    s.textContent = text;
    s.classList.toggle('err', !!isErr);
  }

  // ---------- lanes ----------
  function buildLanes() {
    const root = $('lanes');
    clear(root);
    lanes.forEach((lane) => {
      const sec = el('section', 'lane');
      sec.dataset.lane = lane.def.id;
      sec.appendChild(el('div', 'lane-stripe'));
      const head = el('div', 'lane-head');
      const title = el('div', 'lane-title');
      const name = el('span', 'lane-name', laneName(lane));
      const model = el('span', 'lane-model', lane.def.hint);
      const badge = el('span', 'badge', '1st');
      title.append(name, model, badge);
      const watch = el('div', 'watch');
      const num = el('span', 'watch-num', '0.000');
      const unit = el('span', 'watch-unit', 's');
      const note = el('span', 'watch-note', '');
      watch.append(num, unit, note);
      const stats = el('div', 'stats');
      const stat = (label) => {
        const box = el('div', 'stat');
        box.appendChild(el('span', 'label', label));
        const b = el('b', 'dim', NONE);
        box.appendChild(b);
        stats.appendChild(box);
        return b;
      };
      const cost = stat('Cost');
      const requests = stat('Requests');
      const correct = stat('Correct');
      const tokens = stat('Input tokens');
      const error = el('div', 'lane-error');
      head.append(title, watch, stats, error);
      const body = el('div', 'lane-body');
      body.appendChild(el('div', 'empty', 'Claims appear here when the race starts.'));
      sec.append(head, body);
      root.appendChild(sec);
      lane.dom = { sec, name, model, badge, num, note, cost, requests, correct, tokens, error, body };
    });
  }

  function buildRows(lane, count, texts) {
    const body = lane.dom.body;
    clear(body);
    lane.rows = [];
    for (let i = 0; i < count; i++) {
      const row = el('button', 'row pending');
      row.type = 'button';
      const n = el('span', 'row-n', String(i + 1).padStart(2, '0'));
      const text = el('span', 'row-text');
      if (texts && texts[i]) text.textContent = texts[i];
      else { text.classList.add('skel'); text.style.width = (48 + ((i * 37) % 44)) + '%'; }
      const slot = el('span');
      slot.appendChild(el('span', 'chip wait', 'Checking'));
      const mk = el('span', 'mark');
      row.append(n, text, slot, mk);
      row.addEventListener('click', () => openDrawer(i));
      body.appendChild(row);
      lane.rows.push({ row, text, slot, mk });
    }
  }

  function fillTexts(texts) {
    lanes.forEach((lane) => lane.rows.forEach((r, i) => {
      if (texts[i] && r.text.classList.contains('skel')) {
        r.text.classList.remove('skel');
        r.text.style.width = '';
        r.text.textContent = texts[i];
      }
    }));
  }

  function resetLane(lane) {
    lane.status = 'idle'; lane.result = null; lane.error = null; lane.wall = null; lane.rank = null; lane.costAnim = null;
    const d = lane.dom;
    d.sec.className = 'lane';
    d.badge.classList.remove('on');
    d.num.textContent = '0.000';
    d.note.textContent = '';
    [d.cost, d.requests, d.correct, d.tokens].forEach((b) => { b.textContent = NONE; b.className = 'dim'; });
    d.error.textContent = '';
    d.name.textContent = laneName(lane);
    d.model.textContent = lane.def.hint;
  }

  function labelledCount(result) { return result.verdicts.filter((v) => v.expected !== null && v.expected !== undefined).length; }
  function correctCount(result) { return result.verdicts.filter((v) => v.correct === true).length; }

  function finishLane(lane, result) {
    if (!result || !Array.isArray(result.verdicts)) return failLane(lane, new Error('Malformed response: no verdicts'));
    lane.wall = (performance.now() - state.t0) / 1000;
    lane.status = 'done';
    lane.result = result;
    lane.rank = ++state.finishCount;
    const d = lane.dom;
    d.sec.classList.remove('running');
    d.sec.classList.add('done');
    d.num.textContent = fmtSec(isNum(result.seconds) ? result.seconds : lane.wall);
    d.note.textContent = isNum(result.seconds) ? 'server-measured · ' + lane.wall.toFixed(2) + ' s in browser' : 'browser-measured';
    if (lane.rank === 1) d.badge.classList.add('on');
    if (result.engine) d.model.textContent = result.engine;
    d.requests.textContent = fmtInt(result.requests); d.requests.className = '';
    d.tokens.textContent = fmtInt(result.input_tokens); d.tokens.className = '';
    const labelled = labelledCount(result);
    d.correct.textContent = labelled ? correctCount(result) + '/' + labelled : 'no labels';
    d.correct.className = labelled ? '' : 'dim';
    if (isNum(result.cost_usd)) { lane.costAnim = { start: performance.now(), to: result.cost_usd }; d.cost.className = ''; }
    else d.cost.textContent = 'not reported';

    const texts = result.verdicts.map((v) => v.claim);
    state.claimTexts[state.caseId] = texts;
    if (lane.rows.length !== result.verdicts.length) buildRows(lane, result.verdicts.length, texts);
    fillTexts(texts);
    result.verdicts.forEach((v, i) => {
      const r = lane.rows[i];
      r.row.classList.remove('pending');
      r.text.classList.remove('skel');
      r.text.style.width = '';
      r.text.textContent = v.claim;
      clear(r.slot);
      const c = chip(v.verdict, true);
      c.style.animationDelay = (i * 14) + 'ms';
      r.slot.appendChild(c);
      const m = mark(v.correct, true);
      m.style.animationDelay = (i * 14) + 'ms';
      r.row.replaceChild(m, r.mk);
      r.mk = m;
    });
    if (state.selected !== null) renderDrawer();
  }

  function failLane(lane, err) {
    lane.wall = (performance.now() - state.t0) / 1000;
    lane.status = 'failed';
    lane.error = (err && err.message) ? err.message : String(err);
    const d = lane.dom;
    d.sec.classList.remove('running');
    d.sec.classList.add('failed');
    d.num.textContent = fmtSec(lane.wall);
    d.note.textContent = 'failed · browser-measured';
    d.error.textContent = lane.error;
    lane.rows.forEach((r) => {
      clear(r.slot);
      r.slot.appendChild(chip('error', false));
    });
    if (state.selected !== null) renderDrawer();
  }

  // one animation loop drives every stopwatch and the cost count-up
  let rafOn = false;
  function tick() {
    const now = performance.now();
    let busy = false;
    lanes.forEach((lane) => {
      if (lane.status === 'running') { lane.dom.num.textContent = ((now - state.t0) / 1000).toFixed(3); busy = true; }
      if (lane.costAnim) {
        const k = Math.min(1, (now - lane.costAnim.start) / 450);
        if (k >= 1) { lane.dom.cost.textContent = fmtUsd(lane.costAnim.to); lane.costAnim = null; }
        else { lane.dom.cost.textContent = fmtUsd(lane.costAnim.to * (1 - Math.pow(1 - k, 3))); busy = true; }
      }
    });
    if (upload.t0 !== null) { $('step-parse-v').textContent = ((now - upload.t0) / 1000).toFixed(1) + ' s'; busy = true; }
    if (busy) requestAnimationFrame(tick); else rafOn = false;
  }
  function startTicker() { if (!rafOn) { rafOn = true; requestAnimationFrame(tick); } }

  async function race() {
    if (state.running) return;
    const caseId = $('case').value;
    if (!caseId) return;
    const info = state.cases.find((c) => c.id === caseId);
    state.running = true; state.caseId = caseId; state.finishCount = 0;
    closeDrawer();
    $('race').disabled = true; $('case').disabled = true; $('opponent').disabled = true;
    setStatus('Racing ' + caseId + '…');
    renderResultsIdle('Racing…', 'Three requests left at the same instant. Results appear when all three lanes finish.');
    const texts = state.claimTexts[caseId] || (info && Array.isArray(info.claim_texts) ? info.claim_texts : null);
    const count = texts ? texts.length : (info && isNum(info.claims) ? info.claims : 12);
    lanes.forEach((lane) => {
      lane.engineUsed = engineKey(lane);
      resetLane(lane);
      buildRows(lane, count, texts);
    });
    state.t0 = performance.now();
    const jobs = lanes.map((lane) => {
      lane.status = 'running';
      lane.dom.sec.classList.add('running');
      return api.check(caseId, lane.engineUsed).then((r) => finishLane(lane, r), (e) => failLane(lane, e));
    });
    startTicker();
    await Promise.all(jobs);
    state.running = false;
    $('race').disabled = false; $('case').disabled = false; $('opponent').disabled = false;
    $('race').textContent = 'Race again';
    const failed = lanes.filter((l) => l.status === 'failed').length;
    setStatus(failed ? failed + ' lane' + (failed > 1 ? 's' : '') + ' failed · click a claim for sources' : 'Finished · click any claim to see the sources', failed > 0);
    renderResults();
    recordTally();
  }

  // ---------- results strip ----------
  function renderResultsIdle(line, sub) {
    $('results').classList.add('idle');
    $('verdict-line').textContent = line;
    $('verdict-sub').textContent = sub;
    ['bars-time', 'bars-cost', 'bars-acc'].forEach((id) => clear($(id)));
  }

  function bars(id, title, getValue, format) {
    const box = $(id);
    clear(box);
    box.appendChild(el('span', 'label', title));
    const values = lanes.map((l) => (l.status === 'done' ? getValue(l) : null));
    const max = Math.max(0, ...values.filter(isNum));
    lanes.forEach((lane, i) => {
      const row = el('div', 'bar-row');
      const name = el('span', 'bar-name', lane.def.id === 'frontier' ? laneName(lane) : lane.def.short);
      name.title = laneName(lane);
      const track = el('div', 'bar-track');
      const fill = el('div', 'bar-fill');
      fill.style.background = COLORS[lane.def.id];
      const v = values[i];
      if (isNum(v) && max > 0) fill.style.width = Math.max(0.5, (v / max) * 100) + '%';
      else fill.style.display = 'none';
      track.appendChild(fill);
      const val = el('span', 'bar-val', lane.status === 'failed' ? 'failed' : (isNum(v) ? format(lane, v) : NONE));
      row.append(name, track, val);
      box.appendChild(row);
    });
  }

  function renderResults() {
    const drex = laneById('drex'), rag = laneById('rag'), fr = laneById('frontier');
    const frName = laneName(fr);
    const line = $('verdict-line');
    const sub = $('verdict-sub');
    clear(line);
    $('results').classList.remove('idle');
    const subParts = [];

    const secs = (l) => (isNum(l.result.seconds) ? l.result.seconds : l.wall);
    const okLane = (l) => l.status === 'done' && ((l.result && l.result.verdicts) || []).some((v) => v.verdict !== 'error');
    if ((drex.status === 'done' && !okLane(drex)) || (fr.status === 'done' && !okLane(fr))) {
      line.append('A lane returned only errors, so no speed or cost ratio is shown.');
    } else if (drex.status === 'done' && fr.status === 'done') {
      const ds = secs(drex), fs = secs(fr);
      const dc = drex.result.cost_usd, fc = fr.result.cost_usd;
      const faster = ds > 0 && fs > ds;
      const costKnown = isNum(dc) && isNum(fc) && dc > 0 && fc > 0;
      const cheaper = costKnown && fc > dc;
      const em = (t) => el('em', null, t);
      if (faster && cheaper) {
        line.append('Drex was ', em(fmtRatio(fs / ds) + ' faster'), ' and ', em(fmtRatio(fc / dc) + ' cheaper'), ' than ' + frName + '.');
      } else {
        line.append('Drex was ');
        if (faster) line.append(em(fmtRatio(fs / ds) + ' faster'), ' than ' + frName);
        else line.append('not faster than ' + frName + ' (' + fmtSec(ds) + ' s vs ' + fmtSec(fs) + ' s)');
        if (!costKnown) line.append('. Cost was not reported for both lanes.');
        else if (cheaper) line.append(' and ', em(fmtRatio(fc / dc) + ' cheaper'), '.');
        else line.append(' and not cheaper (' + fmtUsd(dc) + ' vs ' + fmtUsd(fc) + ').');
      }

      const dl = labelledCount(drex.result), fl = labelledCount(fr.result);
      if (dl && fl) {
        const a = correctCount(drex.result), b = correctCount(fr.result);
        subParts.push(a === b && dl === fl ? 'Same accuracy: ' + a + '/' + dl + ' each'
          : 'Accuracy: Drex ' + a + '/' + dl + ', ' + frName + ' ' + b + '/' + fl);
      } else subParts.push('No hand labels for this case, so accuracy is not scored');
    } else {
      const down = [drex, fr].filter((l) => l.status !== 'done').map(laneName).join(' and ');
      line.textContent = 'No head-to-head this run: ' + down + ' did not finish.';
    }

    if (drex.status === 'done' && rag.status === 'done') {
      let missed = 0;
      drex.result.verdicts.forEach((v, i) => {
        const r = rag.result.verdicts[i];
        if (r && v.expected === 'contradicted' && v.verdict === 'contradicted' && r.verdict !== 'contradicted') missed++;
      });
      if (missed) subParts.push('RAG passed ' + missed + ' contradicted claim' + (missed > 1 ? 's' : '') + ' that Drex caught');
    }
    subParts.push(MOCK ? 'MOCK DATA, not measured' : 'measured on this run');
    sub.textContent = subParts.join(' · ');

    bars('bars-time', 'Seconds', secs, (l, v) => fmtSec(v) + ' s');
    bars('bars-cost', 'Cost per case', (l) => l.result.cost_usd, (l, v) => fmtUsd(v));
    bars('bars-acc', 'Correct vs hand labels',
      (l) => (labelledCount(l.result) ? correctCount(l.result) / labelledCount(l.result) : null),
      (l) => correctCount(l.result) + '/' + labelledCount(l.result));
  }

  // ---------- tally (localStorage) ----------
  const TALLY_KEY = 'ctm-race-tally-v1';
  const emptyTally = () => ({ races: 0, seconds: 0, cost: 0, lanes: { drex: { c: 0, n: 0 }, rag: { c: 0, n: 0 }, frontier: { c: 0, n: 0 } } });
  function loadTally() {
    try {
      const t = JSON.parse(localStorage.getItem(TALLY_KEY));
      if (t && isNum(t.races) && isNum(t.seconds) && isNum(t.cost) && t.lanes && t.lanes.drex && t.lanes.rag && t.lanes.frontier) return t;
    } catch (_) { /* storage unavailable or corrupt */ }
    return emptyTally();
  }
  function saveTally(t) { try { localStorage.setItem(TALLY_KEY, JSON.stringify(t)); } catch (_) { /* ignore */ } }
  let tally = loadTally();

  function recordTally() {
    const done = lanes.filter((l) => l.status === 'done');
    if (!done.length) return;
    tally.races += 1;
    done.forEach((l) => {
      tally.seconds += isNum(l.result.seconds) ? l.result.seconds : l.wall;
      if (isNum(l.result.cost_usd)) tally.cost += l.result.cost_usd;
      const slot = tally.lanes[l.def.id];
      slot.c += correctCount(l.result);
      slot.n += labelledCount(l.result);
    });
    saveTally(tally);
    renderTally();
  }
  function renderTally() {
    const box = $('tally');
    clear(box);
    const part = (label, value) => { const s = el('span', null, label + ' '); s.appendChild(el('b', null, value)); box.appendChild(s); };
    part('Races', String(tally.races));
    part('Total', fmtSec(tally.seconds) + ' s');
    part('Spent', fmtUsd(tally.cost));
    LANES.forEach((def) => {
      const s = tally.lanes[def.id];
      part(def.short, s.n ? Math.round((s.c / s.n) * 100) + '% (' + s.c + '/' + s.n + ')' : 'no score yet');
    });
    const wrap = el('span');
    const reset = el('a', null, 'Reset');
    reset.setAttribute('role', 'button'); reset.tabIndex = 0;
    const doReset = () => { tally = emptyTally(); saveTally(tally); renderTally(); };
    reset.addEventListener('click', doReset);
    reset.addEventListener('keydown', (e) => { if (e.key === 'Enter') doReset(); });
    wrap.appendChild(reset);
    box.appendChild(wrap);
  }

  // ---------- detail drawer ----------
  function sourceBox(title, meta, text, isChunk) {
    const box = el('div', 'source');
    const head = el('div', 'source-head');
    head.append(el('span', null, title), el('span', null, meta || ''));
    const pre = el('pre', 'source-text' + (isChunk ? ' chunk' : ''), text);
    box.append(head, pre);
    return { box, pre };
  }

  function loadExhibit(caseId, name) {
    const key = caseId + '|' + name;
    if (!state.exhibitCache.has(key)) {
      const p = api.exhibit(caseId, name).then((r) => String(r && r.text !== undefined ? r.text : ''));
      p.catch(() => state.exhibitCache.delete(key));
      state.exhibitCache.set(key, p);
    }
    return state.exhibitCache.get(key);
  }

  function renderDrawer() {
    const i = state.selected;
    if (i === null) return;
    const body = $('drawer-body');
    const keepScroll = body.scrollTop;
    clear(body);
    $('drawer-title').textContent = 'Claim ' + String(i + 1).padStart(2, '0') + ' · ' + (state.caseId || '');
    const anyV = lanes.map((l) => (l.result ? l.result.verdicts[i] : null)).find(Boolean);
    const texts = state.claimTexts[state.caseId];
    body.appendChild(el('h2', 'd-claim', anyV ? anyV.claim : (texts && texts[i]) || 'Waiting for the first lane…'));

    const hand = el('div', 'd-hand');
    hand.appendChild(el('span', 'label', 'Hand label'));
    if (anyV && anyV.expected) hand.appendChild(chip(anyV.expected));
    else hand.appendChild(el('span', null, anyV ? 'none for this claim' : '…'));
    body.appendChild(hand);

    const drexLane = laneById('drex');
    const drexV = drexLane.result ? drexLane.result.verdicts[i] : null;
    lanes.forEach((lane) => {
      const sec = el('section', 'd-lane');
      const head = el('div', 'd-lane-head');
      const sw = el('span', 'sw');
      sw.style.background = COLORS[lane.def.id];
      head.append(sw, el('strong', null, laneName(lane)));
      const v = lane.result ? lane.result.verdicts[i] : null;
      if (v) head.append(chip(v.verdict), mark(v.correct));
      else head.appendChild(el('span', 'chip ' + (lane.status === 'failed' ? 'error' : 'wait'), lane.status === 'failed' ? 'Error' : lane.status === 'running' ? 'Checking' : 'Not run'));
      sec.appendChild(head);
      if (lane.status === 'failed') sec.appendChild(el('p', 'd-meta', lane.error));
      if (v) {
        sec.appendChild(el('p', 'd-meta', v.source ? 'Cited exhibit: ' + v.source : 'Cited no exhibit'));
        if (v.note) sec.appendChild(el('p', 'd-note', v.note));

        if (lane.def.id === 'drex') {
          if (v.source) {
            const { box, pre } = sourceBox(v.source, 'the exhibit Drex cited', 'Loading exhibit…');
            sec.appendChild(box);
            loadExhibit(state.caseId, v.source).then(
              (t) => { pre.textContent = t || '(empty exhibit)'; },
              (e) => { pre.textContent = 'Could not load this exhibit: ' + (e && e.message ? e.message : e); });
          } else {
            sec.appendChild(el('p', 'd-meta', 'Nothing in the record backs this sentence, so there is no exhibit text to show.'));
          }
        }
        if (lane.def.id === 'rag') {
          const got = Array.isArray(v.retrieved) ? v.retrieved : [];
          if (drexV && drexV.source && got.length && !got.some((c) => c.exhibit === drexV.source)) {
            sec.appendChild(el('p', 'd-flag', 'RAG never retrieved ' + drexV.source + ', the exhibit Drex cited. It judged the claim on the ' + got.length + ' chunk' + (got.length > 1 ? 's' : '') + ' below.'));
          }
          if (!got.length) sec.appendChild(el('p', 'd-meta', 'The server returned no retrieved chunks for this claim.'));
          got.forEach((c, k) => {
            const meta = 'chunk ' + (k + 1) + (isNum(c.score) ? ' · score ' + c.score.toFixed(2) : '');
            sec.appendChild(sourceBox(String(c.exhibit || 'unknown exhibit'), meta, String(c.text || ''), true).box);
          });
        }
      }
      body.appendChild(sec);
    });
    body.scrollTop = keepScroll;
  }

  function openDrawer(i) {
    state.selected = i;
    lanes.forEach((l) => l.rows.forEach((r, k) => r.row.classList.toggle('sel', k === i)));
    $('drawer-body').scrollTop = 0;
    renderDrawer();
    $('drawer').classList.add('on');
    $('drawer').setAttribute('aria-hidden', 'false');
    $('scrim').classList.add('on');
  }
  function closeDrawer() {
    state.selected = null;
    lanes.forEach((l) => l.rows.forEach((r) => r.row.classList.remove('sel')));
    $('drawer').classList.remove('on');
    $('drawer').setAttribute('aria-hidden', 'true');
    if (!$('upload').classList.contains('on')) $('scrim').classList.remove('on');
  }

  // ---------- upload panel ----------
  const upload = { t0: null, busy: false };
  function stepSet(id, cls, value) {
    $(id).className = cls || '';
    if (value !== undefined) $(id + '-v').textContent = value;
  }
  function openUpload() {
    closeDrawer();
    $('upload').classList.add('on');
    $('upload').setAttribute('aria-hidden', 'false');
    $('scrim').classList.add('on');
  }
  function closeUpload() {
    if (upload.busy) return;
    $('upload').classList.remove('on');
    $('upload').setAttribute('aria-hidden', 'true');
    if (state.selected === null) $('scrim').classList.remove('on');
  }
  async function runUpload() {
    if (upload.busy) return;
    const exhibits = Array.from($('up-exhibits').files || []);
    const draft = ($('up-draft').files || [])[0];
    stepSet('step-parse', '', NONE); stepSet('step-ready', '', NONE);
    if (!MOCK && (!exhibits.length || !draft)) {
      stepSet('step-upload', 'fail', 'Choose at least one exhibit PDF and one draft petition');
      return;
    }
    const form = new FormData();
    exhibits.forEach((f) => form.append('exhibits', f, f.name));
    if (draft) form.append('draft', draft, draft.name);
    upload.busy = true;
    $('upload-go').disabled = true; $('upload-close').disabled = true;
    stepSet('step-upload', 'done', exhibits.length + ' exhibit' + (exhibits.length === 1 ? '' : 's') + ' + ' + (draft ? '1 draft' : 'no draft'));
    stepSet('step-parse', 'active', '0.0 s');
    upload.t0 = performance.now();
    startTicker();
    try {
      const r = await api.upload(form);
      upload.t0 = null;
      if (!r || !r.case) throw new Error('The server returned no case id');
      const parse = (isNum(r.parse_seconds) ? fmtSec(r.parse_seconds) + ' s' : 'time not reported') + (isNum(r.parse_cost_usd) ? ' · ' + fmtUsd(r.parse_cost_usd) : '');
      stepSet('step-parse', 'done', parse);
      const nEx = Array.isArray(r.exhibits) ? r.exhibits.length : 0;
      stepSet('step-ready', 'done', r.case + ' · ' + fmtInt(r.claims) + ' claims · ' + nEx + ' exhibits');
      state.cases = state.cases.filter((c) => c.id !== r.case).concat([{ id: r.case, claims: r.claims, exhibits: r.exhibits || [], claim_texts: r.claim_texts }]);
      state.parseInfo[r.case] = parse;
      fillCases(r.case);
      $('race').disabled = state.running;
      setStatus('Added ' + r.case + ' · NDI parsing ' + parse + ' (shared by all lanes)');
    } catch (e) {
      upload.t0 = null;
      stepSet('step-parse', 'fail', (e && e.message) ? e.message : String(e));
    }
    upload.busy = false;
    $('upload-go').disabled = false; $('upload-close').disabled = false;
  }

  // ---------- selects + boot ----------
  function caseLabel(c) {
    return c.id + (isNum(c.claims) ? ' · ' + c.claims + ' claims' : '') + (Array.isArray(c.exhibits) ? ' · ' + c.exhibits.length + ' exhibits' : '');
  }
  function fillCases(selectId) {
    const sel = $('case');
    const keep = selectId || sel.value;
    clear(sel);
    state.cases.forEach((c) => {
      const o = el('option', null, caseLabel(c));
      o.value = c.id;
      sel.appendChild(o);
    });
    if (state.cases.some((c) => c.id === keep)) sel.value = keep;
  }
  function fillOpponents() {
    const sel = $('opponent');
    clear(sel);
    Object.keys(state.baselines).forEach((key) => {
      const o = el('option', null, String(state.baselines[key]));
      o.value = key;
      sel.appendChild(o);
    });
  }

  async function boot() {
    buildLanes();
    renderTally();
    $('race').addEventListener('click', race);
    $('opponent').addEventListener('change', () => {
      const fr = laneById('frontier');
      if (!state.running && fr.status === 'idle') { fr.engineUsed = null; fr.dom.name.textContent = laneName(fr); }
    });
    $('case').addEventListener('change', () => {
      const info = state.parseInfo[$('case').value];
      if (!state.running) setStatus(info ? 'NDI parsing ' + info + ' (shared by all lanes)' : 'Ready');
    });
    $('drawer-close').addEventListener('click', closeDrawer);
    $('scrim').addEventListener('click', () => { closeDrawer(); closeUpload(); });
    $('upload-open').addEventListener('click', openUpload);
    $('upload-close').addEventListener('click', closeUpload);
    $('upload-go').addEventListener('click', runUpload);
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { closeDrawer(); closeUpload(); return; }
      if (state.selected !== null && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
        const n = lanes[0].rows.length;
        if (!n) return;
        e.preventDefault();
        openDrawer((state.selected + (e.key === 'ArrowDown' ? 1 : n - 1)) % n);
      }
    });

    try {
      const data = await api.cases();
      state.cases = Array.isArray(data.cases) ? data.cases : [];
      state.baselines = data.baselines && typeof data.baselines === 'object' ? data.baselines : {};
      fillCases();
      fillOpponents();
      const fr = laneById('frontier');
      fr.dom.name.textContent = laneName(fr);
      if (!state.cases.length) { setStatus('The server returned no cases', true); return; }
      $('race').disabled = false;
      setStatus(MOCK ? 'Mock mode · sample data, no server' : 'Ready');
    } catch (e) {
      setStatus('Cannot load cases: ' + (e && e.message ? e.message : e) + (location.protocol === 'file:' ? ' · open http://127.0.0.1:8765 or add ?mock=1' : ''), true);
      return;
    }

    if (params.get('auto') === '1') {
      await race();
      const open = parseInt(params.get('open') || '', 10);
      if (open >= 1) openDrawer(open - 1);
    }
  }

  boot();
})();
