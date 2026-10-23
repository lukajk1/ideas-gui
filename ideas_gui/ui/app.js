import { api, $, $$, esc, today, tagCounts, isTypingTarget, TagInput, STATUSES, STATUS } from '/ui/common.js';

const POLL_MS = 2000;
const SAVE_DELAY_MS = 600;
const PREFS_KEY = 'ideas-gui-prefs';
const DEFAULT_STATUSES = ['normal', 'promising', 'meh', 'wip'];

const S = {
    ideas: [],
    version: null,
    path: '',
    settings: {},
    editorName: '',
    browserMode: false,
    statuses: new Set(DEFAULT_STATUSES),
    tags: [],          // selected tag filters (all must match)
    q: '',
    tagQ: '',
    sort: 'file',
    shuffle: null,     // Map id -> position while shuffled
    view: 'list',
    selectedId: null,
    visible: [],       // ids in list order
};

// The editor's state: what's on screen vs. what it was loaded from.
const ed = {
    id: null,          // null while writing a new idea that hasn't been saved yet
    isNew: false,
    rev: null,
    dirty: false,
    timer: null,
    inflight: null,
    again: false,
    conflict: null,    // the on-disk idea, when a save was refused
};

const byId = id => S.ideas.find(i => i.id === id);

// ---------------------------------------------------------------------------
// Preferences (per machine, in the webview's local storage)
// ---------------------------------------------------------------------------

function loadPrefs() {
    try {
        const p = JSON.parse(localStorage.getItem(PREFS_KEY) || '{}');
        if (Array.isArray(p.statuses)) S.statuses = new Set(p.statuses.filter(s => STATUS[s]));
        if (Array.isArray(p.tags)) S.tags = p.tags;
        if (p.sort) S.sort = p.sort;
        if (p.view) S.view = p.view;
        if (p.selectedId) S.selectedId = p.selectedId;
    } catch { /* storage unavailable */ }
}

function savePrefs() {
    try {
        localStorage.setItem(PREFS_KEY, JSON.stringify({
            statuses: [...S.statuses], tags: S.tags, sort: S.sort, view: S.view, selectedId: S.selectedId,
        }));
    } catch { /* storage unavailable */ }
}

// ---------------------------------------------------------------------------
// Loading and syncing with the file
// ---------------------------------------------------------------------------

async function loadState() {
    const st = await api('/api/state');
    S.settings = st.settings;
    S.editorName = st.editor;
    S.browserMode = st.browserMode;
    $('#set-version').textContent = `Ideas ${st.appVersion}`;
    if (st.needsSetup) {
        showSetup(st);
        return false;
    }
    $('#setup').hidden = true;
    S.ideas = st.ideas;
    S.version = st.version;
    S.path = st.path;
    $('#sb-file').textContent = st.path;
    return true;
}

async function poll() {
    try {
        if (ed.inflight || $('#setup').hidden === false) return;
        const { version } = await api('/api/version');
        if (version && version !== S.version && !ed.inflight) await refresh(true);
    } catch { /* the app is closing, or a transient error; try again next tick */ }
}

// Re-reads the file, keeping whatever is being edited.
async function refresh(external = false) {
    if (!await loadState()) return;
    if (ed.id) {
        const disk = byId(ed.id);
        if (!disk) {
            if (!ed.dirty) {
                clearEditor();
                toast('The idea you had open was removed from the file.');
            }
        } else if (!ed.dirty && disk.rev !== ed.rev) {
            fillEditor(disk, { keepFocus: true });
        }
    }
    renderAll();
    if (external) flashStatus('Updated from disk');
}

window.refreshSoon = () => setTimeout(() => refresh(), 50);
window.selectIdea = async id => {
    await refresh();
    setView('list');
    if (byId(id)) {
        if (!S.visible.includes(id)) resetFilters();
        await select(id, { focusTitle: false, flash: true });
    }
};

// ---------------------------------------------------------------------------
// Filtering and sorting
// ---------------------------------------------------------------------------

function searchTerms() {
    return S.q.toLowerCase().split(/\s+/).filter(Boolean);
}

function matchesText(idea, terms) {
    if (!terms.length) return true;
    const hay = `${idea.title}\n${idea.description}\n${idea.tags.join(' ')}\n${idea.id}`.toLowerCase();
    return terms.every(t => hay.includes(t));
}

function matchesTags(idea) {
    return S.tags.every(tag => idea.tags.some(t => t.toLowerCase() === tag.toLowerCase()));
}

const STATUS_ORDER = { promising: 0, wip: 1, normal: 2, meh: 3, built: 4 };

function sorted(ideas) {
    const list = [...ideas];
    if (S.shuffle) return list.sort((a, b) => (S.shuffle.get(a.id) ?? 1e9) - (S.shuffle.get(b.id) ?? 1e9));
    const byIndex = (a, b) => a.index - b.index;
    switch (S.sort) {
        case 'newest': return list.sort((a, b) => (b.created || '').localeCompare(a.created || '') || b.index - a.index);
        case 'oldest': return list.sort((a, b) => (a.created || '').localeCompare(b.created || '') || byIndex(a, b));
        case 'updated': return list.sort((a, b) =>
            (b.updated || b.created || '').localeCompare(a.updated || a.created || '') || b.index - a.index);
        case 'title': return list.sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: 'base' }));
        case 'status': return list.sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || byIndex(a, b));
        default: return list.sort(byIndex);
    }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function renderAll() {
    renderSidebar();
    renderList();
    if (S.view === 'stats') renderStats();
}

function renderSidebar() {
    const terms = searchTerms();
    const pool = S.ideas.filter(i => matchesText(i, terms) && matchesTags(i));
    $('#status-filters').innerHTML = STATUSES.map(s => {
        const n = pool.filter(i => i.status === s.key).length;
        const on = S.statuses.has(s.key);
        return `<li class="s-${s.key} ${on ? 'on' : 'off'}" data-status="${s.key}" title="Click to toggle, double-click to show only this">
            <span class="check-box"></span><span class="name">${s.label}</span><span class="n">${n}</span></li>`;
    }).join('');

    const visiblePool = pool.filter(i => S.statuses.has(i.status));
    const inView = new Map(tagCounts(visiblePool));
    const q = S.tagQ.toLowerCase();
    const all = tagCounts(S.ideas).filter(([t]) => !q || t.toLowerCase().includes(q));
    const selected = new Set(S.tags.map(t => t.toLowerCase()));
    all.sort((a, b) => selected.has(b[0].toLowerCase()) - selected.has(a[0].toLowerCase()) ||
        (inView.get(b[0]) || 0) - (inView.get(a[0]) || 0) || b[1] - a[1] || a[0].localeCompare(b[0]));
    $('#tag-filters').innerHTML = all.map(([t]) => {
        const on = selected.has(t.toLowerCase());
        const n = inView.get(t) || 0;
        return `<li class="${on ? 'on' : n ? '' : 'off'}" data-tag="${esc(t)}" title="Click to filter, Ctrl+click to add to the filter">
            <span class="name">${esc(t)}</span>
            <button class="edit" data-rename="${esc(t)}" title="Rename or merge this tag">✎</button>
            <span class="n">${n}</span></li>`;
    }).join('') || `<li class="off"><span class="name">No tags</span></li>`;
    $('#btn-clear-tags').hidden = !S.tags.length;
}

function highlight(text, terms) {
    let html = esc(text);
    for (const t of terms) {
        const re = new RegExp(`(${esc(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'ig');
        html = html.replace(re, '<mark>$1</mark>');
    }
    return html;
}

function snippet(idea, terms) {
    const desc = idea.description.replace(/\s+/g, ' ').trim();
    if (!desc) return '';
    // When a search term is only in the description, show the part around it.
    const lower = desc.toLowerCase();
    const hit = terms.map(t => lower.indexOf(t)).filter(i => i >= 0).sort((a, b) => a - b)[0];
    const start = hit > 60 ? hit - 40 : 0;
    return (start ? '…' : '') + desc.slice(start, start + 180);
}

function shortDate(d) {
    if (!d) return '';
    const [y, m, day] = d.split('-').map(Number);
    const date = new Date(y, m - 1, day);
    const sameYear = y === new Date().getFullYear();
    return date.toLocaleDateString(undefined, sameYear ? { month: 'short', day: 'numeric' } : { month: 'short', year: 'numeric' });
}

function renderList() {
    const terms = searchTerms();
    const textAndTags = S.ideas.filter(i => matchesText(i, terms) && matchesTags(i));
    const shown = sorted(textAndTags.filter(i => S.statuses.has(i.status)));
    S.visible = shown.map(i => i.id);

    const hidden = textAndTags.length - shown.length;
    const hm = $('#hidden-matches');
    hm.hidden = !hidden || !(terms.length || S.tags.length);
    hm.textContent = `+${hidden} in hidden statuses`;

    $('#count').textContent = `${shown.length} ${shown.length === 1 ? 'idea' : 'ideas'}`;
    $('#btn-shuffle').classList.toggle('on', !!S.shuffle);
    $('#sort').value = S.sort;
    $('#sort').disabled = !!S.shuffle;

    $('#idea-list').innerHTML = shown.map(i => {
        const st = STATUS[i.status];
        const snip = snippet(i, terms);
        return `<li class="idea s-${i.status} st-${i.status} ${i.id === S.selectedId ? 'sel' : ''}" data-id="${i.id}">
            <span class="glyph" title="${st.label}">${st.glyph}</span>
            <span class="title">${highlight(i.title, terms)}</span>
            <span class="date">${shortDate(i.created)}</span>
            <div class="sub">${i.tags.length ? `<span class="tags">${i.tags.map(t => highlight(t, terms)).join(' · ')}</span>` : ''}${snip ? `<span class="snippet">${highlight(snip, terms)}</span>` : ''}</div></li>`;
    }).join('');

    const empty = $('#list-empty');
    empty.hidden = shown.length > 0;
    if (!shown.length) {
        empty.innerHTML = S.ideas.length
            ? `<p>No ideas match.</p><button class="link" id="btn-reset-filters">Clear search and filters</button>`
            : `<p>No ideas yet.</p><p>Press <kbd>N</kbd> to write the first one.</p>`;
    }
}

function scrollToSelected() {
    const el = $(`.idea[data-id="${S.selectedId}"]`);
    if (el) el.scrollIntoView({ block: 'nearest' });
}

function resetFilters() {
    S.q = '';
    $('#search').value = '';
    S.tags = [];
    S.statuses = new Set(STATUSES.map(s => s.key));
    S.shuffle = null;
    savePrefs();
    renderAll();
}

// ---------------------------------------------------------------------------
// Editor
// ---------------------------------------------------------------------------

const tagInput = new TagInput($('#f-tags'), {
    suggestions: () => tagCounts(S.ideas).map(([t]) => t),
    onChange: () => markDirty(0),
});
tagInput.input.id = 'f-tags-input';

$('#f-status').innerHTML = STATUSES.map((s, i) =>
    `<button type="button" class="s-${s.key}" data-status="${s.key}" role="radio" title="${s.label} (${i + 1})">
        <span class="glyph">${s.glyph}</span>${s.label}<span class="n">${i + 1}</span></button>`).join('');

function currentStatus() {
    return $('#f-status button.on')?.dataset.status || 'normal';
}

function setStatusButtons(status) {
    $$('#f-status button').forEach(b => {
        b.classList.toggle('on', b.dataset.status === status);
        b.setAttribute('aria-checked', b.dataset.status === status);
    });
}

function readFields() {
    return {
        title: $('#f-title').value,
        status: currentStatus(),
        tags: tagInput.value,
        created: $('#f-created').value,
        description: $('#f-desc').value,
    };
}

function fillEditor(idea, { keepFocus = false } = {}) {
    const setVal = (el, v) => { if (el.value !== v) el.value = v; };
    $('#detail-empty').hidden = true;
    $('#editor').hidden = false;
    $('#conflict').hidden = true;
    ed.conflict = null;
    ed.id = idea.id;
    ed.isNew = false;
    ed.rev = idea.rev;
    ed.dirty = false;
    setVal($('#f-title'), idea.title);
    setStatusButtons(idea.status);
    if (!keepFocus || document.activeElement !== tagInput.input) tagInput.set(idea.tags);
    setVal($('#f-created'), idea.created);
    setVal($('#f-desc'), idea.description);
    updateEditorMeta();
    setSaveState('');
}

function updateEditorMeta() {
    const idea = ed.id && byId(ed.id);
    $('#ed-heading').textContent = ed.isNew ? 'New idea' : idea ? `#${idea.id}` : '';
    $('#btn-delete').hidden = ed.isNew;
    $('#btn-open-editor').hidden = ed.isNew;
    $('#ed-updated').textContent = idea?.updated ? `edited ${relativeTime(idea.updated)}` : '';
}

function relativeTime(iso) {
    const then = new Date(iso);
    const s = (Date.now() - then) / 1000;
    if (s < 60) return 'just now';
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function clearEditor() {
    clearTimeout(ed.timer);
    Object.assign(ed, { id: null, isNew: false, rev: null, dirty: false, conflict: null });
    S.selectedId = null;
    $('#editor').hidden = true;
    $('#detail-empty').hidden = false;
    savePrefs();
}

function setSaveState(state, text) {
    const el = $('#ed-save');
    el.className = `save-state ${state}`;
    el.textContent = text ?? { saving: 'Saving…', saved: 'Saved', unsaved: 'Unsaved', '': '' }[state] ?? '';
}

function markDirty(delay = SAVE_DELAY_MS) {
    if ($('#editor').hidden) return;
    ed.dirty = true;
    clearTimeout(ed.timer);
    if (ed.isNew && !$('#f-title').value.trim()) {
        setSaveState('unsaved', 'Give it a title to save it');
        return;
    }
    setSaveState('unsaved');
    ed.timer = setTimeout(saveNow, delay);
}

async function saveNow() {
    clearTimeout(ed.timer);
    if (ed.inflight) { ed.again = true; return ed.inflight; }
    if (!ed.dirty || ed.conflict) return;
    const fields = readFields();
    if (!fields.title.trim()) {
        setSaveState('error', ed.isNew ? 'Give it a title to save it' : 'The title can’t be empty');
        return;
    }
    ed.dirty = false;
    setSaveState('saving');
    ed.inflight = (async () => {
        try {
            const res = ed.isNew
                ? await api('/api/ideas/create', { fields })
                : await api('/api/ideas/update', { id: ed.id, rev: ed.rev, fields });
            const idea = res.idea;
            const at = S.ideas.findIndex(i => i.id === idea.id);
            if (at >= 0) S.ideas[at] = idea; else S.ideas.push(idea);
            S.version = res.version;
            if (ed.isNew) {
                ed.isNew = false;
                ed.id = idea.id;
                S.selectedId = idea.id;
                savePrefs();
            }
            ed.rev = idea.rev;
            setSaveState(ed.dirty ? 'unsaved' : 'saved');
            updateEditorMeta();
            renderSidebar();
            renderList();
        } catch (e) {
            ed.dirty = true;
            if (e.status === 409 && e.data?.conflict) {
                ed.conflict = e.data.idea;
                $('#conflict').hidden = false;
                setSaveState('error', 'Not saved — see above');
            } else {
                setSaveState('error', `Not saved: ${e.message}`);
                toast(`Couldn’t save: ${e.message}`, { error: true });
            }
            throw e;
        } finally {
            ed.inflight = null;
            if (ed.again) {
                ed.again = false;
                if (ed.dirty && !ed.conflict) setTimeout(saveNow, 0);
            }
        }
    })();
    return ed.inflight.catch(() => {});
}

// Saves the open idea before something else replaces it. Returns false if it couldn't be saved.
async function settle() {
    if (ed.inflight) await ed.inflight.catch(() => {});
    if (ed.dirty && !ed.conflict) {
        if (ed.isNew && !$('#f-title').value.trim()) return true;  // an empty new idea: just drop it
        await saveNow();
        if (ed.inflight) await ed.inflight.catch(() => {});
    }
    if (ed.dirty) {
        toast('Your changes to this idea aren’t saved yet.', { error: true });
        return false;
    }
    return true;
}

async function select(id, { focusTitle = false, flash = false } = {}) {
    if (id === ed.id && !ed.isNew) {
        if (focusTitle) $('#f-title').focus();
        return;
    }
    if (!await settle()) return;
    const idea = byId(id);
    if (!idea) return;
    S.selectedId = id;
    savePrefs();
    fillEditor(idea);
    $$('.idea.sel').forEach(el => el.classList.remove('sel'));
    const row = $(`.idea[data-id="${id}"]`);
    if (row) {
        row.classList.add('sel');
        row.scrollIntoView({ block: 'nearest' });
        if (flash) { row.classList.remove('flash'); void row.offsetWidth; row.classList.add('flash'); }
    }
    if (focusTitle) $('#f-title').focus();
}

async function newIdea(fields = {}) {
    if (!await settle()) return;
    setView('list');
    S.selectedId = null;
    $$('.idea.sel').forEach(el => el.classList.remove('sel'));
    fillEditor({ id: null, rev: null, title: '', status: 'normal', tags: [...S.tags], created: today(), description: '', ...fields });
    ed.isNew = true;
    ed.id = null;
    updateEditorMeta();
    setSaveState('', 'Saves once it has a title');
    $('#f-title').focus();
}

function moveSelection(delta) {
    if (!S.visible.length) return;
    let at = S.visible.indexOf(S.selectedId);
    at = at < 0 ? (delta > 0 ? 0 : S.visible.length - 1) : Math.max(0, Math.min(S.visible.length - 1, at + delta));
    select(S.visible[at]);
}

function randomIdea() {
    const pool = S.visible.filter(id => id !== S.selectedId);
    if (!pool.length) return toast('Nothing to pick from — loosen the filters.');
    setView('list');
    select(pool[Math.floor(Math.random() * pool.length)], { flash: true });
}

function setStatus(status) {
    if ($('#editor').hidden) return;
    setStatusButtons(status);
    markDirty(0);
}

async function deleteSelected() {
    if (!ed.id || ed.isNew) return;
    const idea = byId(ed.id);
    if (!idea) return;
    if (!await confirmDialog(`Delete “${idea.title}”?`)) return;
    if (ed.inflight) await ed.inflight.catch(() => {});
    try {
        const at = S.visible.indexOf(idea.id);
        const res = await api('/api/ideas/delete', { id: idea.id, rev: ed.dirty ? null : ed.rev });
        S.ideas = S.ideas.filter(i => i.id !== idea.id);
        S.version = res.version;
        clearEditor();
        renderAll();
        const next = S.visible[Math.min(at, S.visible.length - 1)];
        if (next) select(next);
        toast(`Deleted “${res.title}”.`, {
            action: 'Undo', timeout: 10000,
            onAction: async () => {
                const r = await api('/api/ideas/undo', { token: res.token });
                await refresh();
                select(r.idea.id, { flash: true });
            },
        });
    } catch (e) {
        if (e.status === 409) toast('It changed on disk first; check it and try again.', { error: true });
        else toast(`Couldn’t delete: ${e.message}`, { error: true });
        refresh();
    }
}

// ---------------------------------------------------------------------------
// Stats
// ---------------------------------------------------------------------------

function renderStats() {
    const ideas = S.ideas;
    const count = s => ideas.filter(i => i.status === s).length;
    const cutoff = new Date(Date.now() - 30 * 86400e3).toISOString().slice(0, 10);
    const recent = ideas.filter(i => i.created && i.created >= cutoff).length;
    const tiles = [
        ['Ideas', ideas.length], ['Promising', count('promising')], ['In progress', count('wip')],
        ['Built', count('built')], ['Added, last 30 days', recent],
    ];

    const maxStatus = Math.max(1, ...STATUSES.map(s => count(s.key)));
    const statusBars = STATUSES.map(s => {
        const n = count(s.key);
        return `<span class="lbl s-${s.key}" data-status-only="${s.key}">${s.glyph} ${s.label}</span>
            <div class="track"><div class="bar s-${s.key}" data-status-only="${s.key}" data-tip="${s.label}: ${n} ideas" style="width:${n / maxStatus * 100}%"></div></div>
            <span class="val">${n}</span>`;
    }).join('');

    const tags = tagCounts(ideas).slice(0, 14);
    const maxTag = Math.max(1, ...tags.map(t => t[1]));
    const tagBars = tags.map(([t, n]) => `<span class="lbl" data-tag-only="${esc(t)}" title="${esc(t)}">${esc(t)}</span>
        <div class="track"><div class="bar" data-tag-only="${esc(t)}" data-tip="${esc(t)}: ${n} ideas" style="width:${n / maxTag * 100}%"></div></div>
        <span class="val">${n}</span>`).join('');

    $('#stats').innerHTML = `<div class="stats-inner">
        <div class="tiles">${tiles.map(([k, v]) => `<div class="tile"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('')}</div>
        <div class="card"><h2>By status</h2><p class="sub">Click one to list just those.</p><div class="hbars">${statusBars}</div></div>
        <div class="card"><h2>Top tags</h2><p class="sub">${tagCounts(ideas).length} tags in all. Click one to filter by it.</p><div class="hbars">${tagBars}</div></div>
        <div class="card full"><h2>Ideas added per month</h2><p class="sub" id="months-sub"></p><div class="months" id="months"></div></div>
    </div>`;
    renderMonths(ideas);
}

function renderMonths(ideas) {
    const dated = ideas.filter(i => /^\d{4}-\d{2}/.test(i.created));
    const undated = ideas.length - dated.length;
    $('#months-sub').textContent = undated
        ? `${dated.length} ideas with a date. ${undated} older ones have no date and aren’t counted.`
        : `${dated.length} ideas.`;
    if (!dated.length) return;

    const counts = new Map();
    for (const i of dated) counts.set(i.created.slice(0, 7), (counts.get(i.created.slice(0, 7)) || 0) + 1);
    const keys = [...counts.keys()].sort();
    const months = [];
    let [y, m] = keys[0].split('-').map(Number);
    const now = new Date();
    const endKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
    const last = keys[keys.length - 1] > endKey ? keys[keys.length - 1] : endKey;
    while (true) {
        const key = `${y}-${String(m).padStart(2, '0')}`;
        months.push([key, counts.get(key) || 0]);
        if (key >= last) break;
        if (++m > 12) { m = 1; y++; }
    }

    const W = Math.max(400, $('#months').clientWidth || 800), H = 200, padL = 28, padB = 22, padT = 8;
    const max = Math.max(...months.map(x => x[1]));
    const step = Math.ceil(max / 4) || 1;
    const top = step * 4;
    const slot = (W - padL) / months.length;
    const bw = Math.max(2, Math.min(40, slot - 4));
    const yOf = v => padT + (H - padB - padT) * (1 - v / top);
    let svg = '';
    for (let v = step; v <= top; v += step) {
        svg += `<line class="grid" x1="${padL}" x2="${W}" y1="${yOf(v)}" y2="${yOf(v)}"/><text x="${padL - 6}" y="${yOf(v) + 4}" text-anchor="end">${v}</text>`;
    }
    svg += `<line class="axis" x1="${padL}" x2="${W}" y1="${yOf(0)}" y2="${yOf(0)}"/>`;
    const labelEvery = Math.ceil(months.length / Math.floor((W - padL) / 54));
    months.forEach(([key, n], i) => {
        const x = padL + i * slot + (slot - bw) / 2;
        const label = new Date(+key.slice(0, 4), +key.slice(5) - 1, 1)
            .toLocaleDateString(undefined, { month: 'short', year: 'numeric' });
        if (n) {
            const h = yOf(0) - yOf(n);
            const r = Math.min(4, bw / 2, h);
            // Bar with rounded top corners, square at the baseline.
            svg += `<path class="bar" d="M${x},${yOf(0)} V${yOf(n) + r} Q${x},${yOf(n)} ${x + r},${yOf(n)} H${x + bw - r} Q${x + bw},${yOf(n)} ${x + bw},${yOf(n) + r} V${yOf(0)} Z"/>`;
        }
        svg += `<rect class="hit" x="${padL + i * slot}" y="${padT}" width="${slot}" height="${H - padB - padT}" data-tip="${label}: ${n} ${n === 1 ? 'idea' : 'ideas'}"/>`;
        if (i % labelEvery === 0) {
            svg += `<text x="${padL + i * slot + slot / 2}" y="${H - 6}" text-anchor="middle">${label}</text>`;
        }
    });
    $('#months').innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Ideas added per month">${svg}</svg>`;
}

// ---------------------------------------------------------------------------
// Views, dialogs, toasts
// ---------------------------------------------------------------------------

function setView(view) {
    S.view = view;
    savePrefs();
    $$('#view-switch button').forEach(b => b.classList.toggle('on', b.dataset.view === view));
    if (!$('#setup').hidden) return;
    $('#layout').hidden = view !== 'list';
    $('#stats').hidden = view !== 'stats';
    if (view === 'stats') renderStats();
}

function showSetup(st) {
    $('#layout').hidden = true;
    $('#stats').hidden = true;
    $('#setup').hidden = false;
    const broken = !!st.loadError;
    $('#setup-title').textContent = broken ? 'The ideas file couldn’t be loaded' : 'Set up your ideas file';
    $('#setup-text').textContent = broken
        ? `Nothing has been changed. Fix the file, restore it from a backup, or pick another file. ${st.path}`
        : 'Ideas keeps everything in one JSON file. Put it in a synced folder (like Google Drive) to have it everywhere.';
    $('#setup-error').hidden = !broken;
    $('#setup-error').textContent = st.loadError || '';
    $('#setup-links').hidden = !broken;
}

let toastTimer = 0;
function toast(text, { error = false, action, onAction, timeout = 4500 } = {}) {
    const el = document.createElement('div');
    el.className = `toast ${error ? 'error' : ''}`;
    el.innerHTML = `<span>${esc(text)}</span>`;
    if (action) {
        const b = document.createElement('button');
        b.textContent = action;
        b.onclick = () => { el.remove(); onAction(); };
        el.appendChild(b);
    }
    $('#toasts').appendChild(el);
    setTimeout(() => el.remove(), timeout);
}

function flashStatus(text) {
    $('#sb-msg').textContent = text;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $('#sb-msg').textContent = ''; }, 3000);
}

function confirmDialog(text) {
    const dlg = $('#dlg-confirm');
    $('#confirm-text').textContent = text;
    dlg.returnValue = '';
    dlg.showModal();
    $('#confirm-ok').focus();
    return new Promise(resolve => dlg.addEventListener('close', () => resolve(dlg.returnValue === 'ok'), { once: true }));
}

async function renameTag(tag) {
    const dlg = $('#dlg-rename');
    const n = S.ideas.filter(i => i.tags.some(t => t.toLowerCase() === tag.toLowerCase())).length;
    $('#rename-text').textContent = `“${tag}” is on ${n} ${n === 1 ? 'idea' : 'ideas'}.`;
    $('#rename-input').value = tag;
    $('#rename-tags').innerHTML = tagCounts(S.ideas).map(([t]) => `<option value="${esc(t)}">`).join('');
    dlg.returnValue = '';
    dlg.showModal();
    $('#rename-input').select();
    await new Promise(r => dlg.addEventListener('close', r, { once: true }));
    if (dlg.returnValue !== 'ok') return;
    const to = $('#rename-input').value.trim();
    if (to === tag) return;
    if (!await settle()) return;
    try {
        const res = await api('/api/tags/rename', { old: tag, new: to });
        S.tags = S.tags.map(t => t.toLowerCase() === tag.toLowerCase() ? to : t).filter(Boolean);
        await refresh();
        if (ed.id && byId(ed.id)) fillEditor(byId(ed.id));
        toast(to ? `Renamed “${tag}” to “${to}” on ${res.changed} ideas.` : `Removed “${tag}” from ${res.changed} ideas.`);
    } catch (e) {
        toast(`Couldn’t rename: ${e.message}`, { error: true });
    }
}

// -- settings --

function openSettings() {
    $('#set-path').textContent = S.path || '(none)';
    $('#set-hotkey').value = S.settings.hotkey || '';
    $('#set-tray').checked = !!S.settings.close_to_tray;
    $('#set-editor').value = S.settings.editor || '';
    $('#set-editor-now').textContent = S.editorName;
    $('#hotkey-error').hidden = true;
    if (S.browserMode) $('#hotkey-help').textContent = 'The shortcut and tray icon only work in the desktop app.';
    $('#dlg-settings').showModal();
}

async function saveSetting(changes) {
    try {
        const res = await api('/api/settings', { settings: changes });
        S.settings = res.settings;
        const st = await api('/api/state');
        S.editorName = st.editor;
        $('#set-editor-now').textContent = S.editorName;
        return true;
    } catch (e) {
        return e.message;
    }
}

const hotkeyInput = $('#set-hotkey');
hotkeyInput.addEventListener('focus', () => { hotkeyInput.placeholder = 'Press a shortcut…'; hotkeyInput.dataset.prev = hotkeyInput.value; hotkeyInput.value = ''; });
hotkeyInput.addEventListener('blur', () => {
    hotkeyInput.placeholder = 'Off';
    if (!hotkeyInput.value) hotkeyInput.value = hotkeyInput.dataset.prev || '';
});
hotkeyInput.addEventListener('keydown', async e => {
    if (e.key === 'Tab') return;
    e.preventDefault();
    e.stopPropagation();
    if (e.key === 'Escape') { hotkeyInput.blur(); return; }
    if (['Control', 'Alt', 'Shift', 'Meta'].includes(e.key)) return;
    const mods = [e.ctrlKey && 'Ctrl', e.altKey && 'Alt', e.shiftKey && 'Shift', e.metaKey && 'Win'].filter(Boolean);
    let key = e.code.startsWith('Key') ? e.code.slice(3) : e.code.startsWith('Digit') ? e.code.slice(5) : e.key;
    if (key === ' ') key = 'Space';
    if (!mods.length) {
        $('#hotkey-error').hidden = false;
        $('#hotkey-error').textContent = 'Include Ctrl, Alt, Shift or Win.';
        return;
    }
    const combo = [...mods, key.length === 1 ? key.toUpperCase() : key].join('+');
    hotkeyInput.value = combo;
    const result = await saveSetting({ hotkey: combo });
    $('#hotkey-error').hidden = result === true;
    if (result !== true) {
        $('#hotkey-error').textContent = result;
        hotkeyInput.value = hotkeyInput.dataset.prev || '';
    } else {
        hotkeyInput.dataset.prev = combo;
        hotkeyInput.blur();
    }
});
$('#btn-hotkey-clear').onclick = async () => {
    await saveSetting({ hotkey: '' });
    hotkeyInput.value = '';
    hotkeyInput.dataset.prev = '';
};
$('#set-tray').onchange = e => saveSetting({ close_to_tray: e.target.checked });
$('#set-editor').onchange = e => saveSetting({ editor: e.target.value.trim().replace(/^"|"$/g, '') });

async function chooseFile(mode) {
    if (!await settle()) return;
    try {
        const res = await api('/api/file/choose', { mode });
        if (res.cancelled) return;
        clearEditor();
        S.shuffle = null;
        const ok = await loadState();
        $('#dlg-settings').close();
        if (ok) { setView(S.view); renderAll(); }
        toast(res.note || `Now using ${res.path}`);
    } catch (e) {
        toast(e.message, { error: true });
    }
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

let searchTimer = 0;
$('#search').addEventListener('input', e => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { S.q = e.target.value; renderSidebar(); renderList(); }, 80);
});
$('#search').addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        if (e.target.value) { e.target.value = ''; S.q = ''; renderAll(); } else e.target.blur();
        e.stopPropagation();
    } else if (e.key === 'Enter' || e.key === 'ArrowDown') {
        e.preventDefault();
        if (S.visible.length) select(S.visible[0]);
        $('#idea-list').focus();
    }
});
$('#tag-search').addEventListener('input', e => { S.tagQ = e.target.value; renderSidebar(); });
$('#tag-search').addEventListener('keydown', e => { if (e.key === 'Escape') { e.target.value = ''; S.tagQ = ''; renderSidebar(); e.target.blur(); } });

$('#status-filters').addEventListener('click', e => {
    const li = e.target.closest('li[data-status]');
    if (!li) return;
    const s = li.dataset.status;
    if (S.statuses.has(s)) S.statuses.delete(s); else S.statuses.add(s);
    savePrefs();
    renderAll();
});
$('#status-filters').addEventListener('dblclick', e => {
    const li = e.target.closest('li[data-status]');
    if (!li) return;
    S.statuses = new Set([li.dataset.status]);
    savePrefs();
    renderAll();
});
$('#btn-all-status').onclick = () => {
    const all = S.statuses.size === STATUSES.length;
    S.statuses = new Set(all ? DEFAULT_STATUSES : STATUSES.map(s => s.key));
    savePrefs();
    renderAll();
};
$('#tag-filters').addEventListener('click', e => {
    const rn = e.target.closest('[data-rename]');
    if (rn) { e.stopPropagation(); renameTag(rn.dataset.rename); return; }
    const li = e.target.closest('li[data-tag]');
    if (!li) return;
    const tag = li.dataset.tag;
    const has = S.tags.some(t => t.toLowerCase() === tag.toLowerCase());
    if (e.ctrlKey || e.metaKey || e.shiftKey) {
        S.tags = has ? S.tags.filter(t => t.toLowerCase() !== tag.toLowerCase()) : [...S.tags, tag];
    } else {
        S.tags = has && S.tags.length === 1 ? [] : [tag];
    }
    savePrefs();
    renderAll();
});
$('#btn-clear-tags').onclick = () => { S.tags = []; savePrefs(); renderAll(); };
$('#hidden-matches').onclick = () => { S.statuses = new Set(STATUSES.map(s => s.key)); savePrefs(); renderAll(); };

$('#idea-list').addEventListener('click', e => {
    const li = e.target.closest('.idea');
    if (li) select(li.dataset.id);
});
$('#idea-list').addEventListener('dblclick', e => {
    if (e.target.closest('.idea')) $('#f-title').focus();
});
$('#list-empty').addEventListener('click', e => { if (e.target.id === 'btn-reset-filters') resetFilters(); });

$('#sort').onchange = e => { S.sort = e.target.value; S.shuffle = null; savePrefs(); renderList(); scrollToSelected(); };
// Shuffles (again, if already shuffled). Picking a sort, or right-clicking the button, turns it off.
function toggleShuffle() {
    const ids = S.ideas.map(i => i.id);
    for (let i = ids.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [ids[i], ids[j]] = [ids[j], ids[i]];
    }
    S.shuffle = new Map(ids.map((id, n) => [id, n]));
    renderList();
    $('#idea-list').scrollTop = 0;
}
$('#btn-shuffle').onclick = toggleShuffle;
$('#btn-shuffle').oncontextmenu = e => { e.preventDefault(); S.shuffle = null; renderList(); };
$('#btn-shuffle').title = 'Shuffle (S). Click again to reshuffle; pick a sort to go back.';

$('#btn-new').onclick = () => newIdea();
$('#btn-random').onclick = randomIdea;
$$('#view-switch button').forEach(b => b.onclick = () => setView(b.dataset.view));
$('#btn-settings').onclick = openSettings;
$('#btn-help').onclick = () => $('#dlg-help').showModal();
$('#sb-file').onclick = () => api('/api/open/folder', { which: 'file' }).catch(e => toast(e.message, { error: true }));
$$('[data-choose]').forEach(b => b.onclick = () => chooseFile(b.dataset.choose));
$$('[data-folder]').forEach(b => b.onclick = () => api('/api/open/folder', { which: b.dataset.folder }).catch(e => toast(e.message, { error: true })));

$('#f-title').addEventListener('input', () => markDirty());
$('#f-title').addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); saveNow(); $('#f-desc').focus(); }
});
$('#f-desc').addEventListener('input', () => markDirty());
$('#f-created').addEventListener('change', () => markDirty(0));
$('#f-status').addEventListener('click', e => {
    const b = e.target.closest('button[data-status]');
    if (b) setStatus(b.dataset.status);
});
$('#editor').addEventListener('focusout', () => { if (ed.dirty) markDirty(150); });

$('#btn-delete').onclick = deleteSelected;
$('#btn-open-editor').onclick = async () => {
    await settle();
    api('/api/open/editor', { id: ed.id }).catch(e => toast(e.message, { error: true }));
};
$('#btn-take-theirs').onclick = () => {
    const disk = ed.conflict;
    const at = S.ideas.findIndex(i => i.id === disk.id);
    if (at >= 0) S.ideas[at] = disk;
    fillEditor(disk);
    renderList();
};
$('#btn-keep-mine').onclick = () => {
    ed.rev = ed.conflict.rev;
    ed.conflict = null;
    $('#conflict').hidden = true;
    ed.dirty = true;
    saveNow();
};

$('#stats').addEventListener('click', e => {
    const st = e.target.closest('[data-status-only]');
    const tg = e.target.closest('[data-tag-only]');
    if (!st && !tg) return;
    S.q = ''; $('#search').value = ''; S.shuffle = null;
    if (st) { S.statuses = new Set([st.dataset.statusOnly]); S.tags = []; }
    if (tg) { S.tags = [tg.dataset.tagOnly]; S.statuses = new Set(STATUSES.map(s => s.key)); }
    savePrefs();
    setView('list');
    renderAll();
});

// Tooltips for chart marks
const tip = $('#tooltip');
document.addEventListener('mousemove', e => {
    const t = e.target.closest?.('[data-tip]');
    if (!t) { tip.hidden = true; return; }
    tip.textContent = t.dataset.tip;
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    tip.style.left = `${Math.min(window.innerWidth - r.width - 8, e.clientX + 12)}px`;
    tip.style.top = `${e.clientY - r.height - 10}px`;
});

window.addEventListener('resize', () => { if (S.view === 'stats') renderMonths(S.ideas); });

document.addEventListener('keydown', e => {
    if (document.querySelector('dialog[open]')) return;
    const typing = isTypingTarget(document.activeElement);
    const ctrl = e.ctrlKey || e.metaKey;

    if (ctrl && e.key.toLowerCase() === 's') { e.preventDefault(); saveNow(); return; }
    if (ctrl && e.key.toLowerCase() === 'f') { e.preventDefault(); $('#search').focus(); $('#search').select(); return; }
    if (ctrl && !e.shiftKey && /^[1-5]$/.test(e.key)) { e.preventDefault(); setStatus(STATUSES[+e.key - 1].key); return; }
    if (ctrl && e.key.toLowerCase() === 'n') { e.preventDefault(); newIdea(); return; }

    if (e.key === 'Escape' && typing) {
        if (ed.dirty) markDirty(0);
        document.activeElement.blur();
        $('#idea-list').focus();
        return;
    }
    if (typing || ctrl || e.altKey) return;

    const k = e.key;
    if (k === '/') { e.preventDefault(); $('#search').focus(); $('#search').select(); }
    else if (k === 'n' || k === 'N') { e.preventDefault(); newIdea(); }
    else if (k === 'r' || k === 'R') randomIdea();
    else if (k === 's' || k === 'S') { setView('list'); toggleShuffle(); }
    else if (k === 'v' || k === 'V') setView(S.view === 'list' ? 'stats' : 'list');
    else if (k === 'j' || k === 'ArrowDown') { e.preventDefault(); moveSelection(1); }
    else if (k === 'k' || k === 'ArrowUp') { e.preventDefault(); moveSelection(-1); }
    else if (k === 'Home') { e.preventDefault(); if (S.visible.length) select(S.visible[0]); }
    else if (k === 'End') { e.preventDefault(); if (S.visible.length) select(S.visible[S.visible.length - 1]); }
    else if (k === 'Enter' && ed.id) { e.preventDefault(); $('#f-title').focus(); }
    else if (/^[1-5]$/.test(k)) setStatus(STATUSES[+k - 1].key);
    else if ((k === 'e' || k === 'E') && ed.id) $('#btn-open-editor').click();
    else if (k === 'Delete') deleteSelected();
    else if (k === '?') $('#dlg-help').showModal();
});

window.addEventListener('beforeunload', () => { if (ed.dirty) saveNow(); });

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

(async () => {
    loadPrefs();
    try {
        if (await loadState()) {
            renderAll();
            setView(S.view);
            if (S.selectedId && byId(S.selectedId)) select(S.selectedId);
            else S.selectedId = null;
            scrollToSelected();
        } else {
            setView(S.view);
        }
    } catch (e) {
        toast(`Couldn’t load: ${e.message}`, { error: true });
    }
    // Each check starts after the last one finished, and none run while the window is hidden
    // (minimized, or closed to the tray for days): it checks again the moment it shows.
    let pollTimer;
    const pollLoop = async () => {
        clearTimeout(pollTimer);
        if (!document.hidden) await poll();
        pollTimer = setTimeout(pollLoop, POLL_MS);
    };
    document.addEventListener('visibilitychange', () => { if (!document.hidden) pollLoop(); });
    pollTimer = setTimeout(pollLoop, POLL_MS);
    setInterval(() => { if (ed.id && !document.hidden) updateEditorMeta(); }, 60000);
})();
