// Shared by the main window and the quick-capture window.

// Keys 1-5 follow this order.
export const STATUSES = [
    { key: 'normal',    label: 'Normal',    glyph: '–' },
    { key: 'promising', label: 'Promising', glyph: '★' },
    { key: 'meh',       label: 'Meh',       glyph: '~' },
    { key: 'wip',       label: 'WIP',       glyph: '▶' },
    { key: 'built',     label: 'Built',     glyph: '✓' },
];
export const STATUS = Object.fromEntries(STATUSES.map((s, i) => [s.key, { ...s, n: i + 1 }]));

export async function api(path, data) {
    const opts = data === undefined ? {} : {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Ideas': '1' },
        body: JSON.stringify(data),
    };
    const res = await fetch(path, opts);
    const body = await res.json().catch(() => ({ error: res.statusText }));
    if (!res.ok) {
        const err = new Error(body.error || res.statusText);
        err.status = res.status;
        err.data = body;
        throw err;
    }
    return body;
}

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c =>
        ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

export function today() {
    const d = new Date();
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

export function tagCounts(ideas) {
    const counts = new Map();
    for (const idea of ideas) {
        for (const t of idea.tags) counts.set(t, (counts.get(t) || 0) + 1);
    }
    return [...counts].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

export function isTypingTarget(el) {
    return el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT' || el.isContentEditable);
}

/**
 * Tags as removable chips plus a text box with suggestions.
 * Enter, Tab or comma commits; Backspace on an empty box removes the last chip.
 */
export class TagInput {
    constructor(root, { suggestions = () => [], onChange = () => {}, placeholder = 'Add tags…' } = {}) {
        this.root = root;
        this.suggestions = suggestions;
        this.onChange = onChange;
        this.tags = [];
        this.active = -1;
        root.classList.add('tag-input');
        root.innerHTML = `<span class="chips"></span><input type="text" spellcheck="false" placeholder="${esc(placeholder)}"><ul class="suggest" hidden></ul>`;
        this.chips = root.querySelector('.chips');
        this.input = root.querySelector('input');
        this.list = root.querySelector('.suggest');

        root.addEventListener('mousedown', e => {
            if (e.target === root || e.target === this.chips) { e.preventDefault(); this.input.focus(); }
        });
        this.chips.addEventListener('click', e => {
            const btn = e.target.closest('button[data-i]');
            if (btn) { this.remove(+btn.dataset.i); this.input.focus(); }
        });
        this.list.addEventListener('mousedown', e => {
            const li = e.target.closest('li');
            if (li) { e.preventDefault(); this.add(li.dataset.tag); this.input.value = ''; this.refresh(); }
        });
        this.input.addEventListener('input', () => {
            if (this.input.value.includes(',')) {
                const parts = this.input.value.split(',');
                this.input.value = parts.pop();
                parts.forEach(p => this.add(p));
            }
            this.active = this.input.value.trim() ? 0 : -1;
            this.refresh();
        });
        this.input.addEventListener('keydown', e => this.onKey(e));
        this.input.addEventListener('focus', () => this.refresh());
        this.input.addEventListener('blur', () => { this.commit(); this.list.hidden = true; });
    }

    get value() { return [...this.tags]; }

    set(tags) {
        this.tags = [...tags];
        this.input.value = '';
        this.render();
    }

    add(tag) {
        tag = String(tag).trim();
        if (!tag || this.tags.some(t => t.toLowerCase() === tag.toLowerCase())) return;
        this.tags.push(tag);
        this.render();
        this.onChange(this.value);
    }

    remove(i) {
        this.tags.splice(i, 1);
        this.render();
        this.onChange(this.value);
    }

    commit() {
        if (this.input.value.trim()) {
            this.add(this.input.value);
            this.input.value = '';
        }
    }

    matches() {
        const q = this.input.value.trim().toLowerCase();
        const taken = new Set(this.tags.map(t => t.toLowerCase()));
        return this.suggestions()
            .filter(t => !taken.has(t.toLowerCase()) && (!q || t.toLowerCase().includes(q)))
            .sort((a, b) => (q && b.toLowerCase().startsWith(q)) - (q && a.toLowerCase().startsWith(q)))
            .slice(0, 8);
    }

    refresh() {
        const items = document.activeElement === this.input ? this.matches() : [];
        this.items = items;
        if (this.active >= items.length) this.active = items.length - 1;
        this.list.hidden = !items.length;
        this.list.innerHTML = items.map((t, i) =>
            `<li data-tag="${esc(t)}" class="${i === this.active ? 'on' : ''}">${esc(t)}</li>`).join('');
    }

    onKey(e) {
        const open = !this.list.hidden && this.items?.length;
        if (e.key === 'ArrowDown' && open) {
            e.preventDefault(); this.active = (this.active + 1) % this.items.length; this.refresh();
        } else if (e.key === 'ArrowUp' && open) {
            e.preventDefault(); this.active = (this.active - 1 + this.items.length) % this.items.length; this.refresh();
        } else if (e.key === 'Enter' || (e.key === 'Tab' && this.input.value.trim())) {
            if (!this.input.value.trim() && this.active < 0) return;  // let Enter reach the form
            e.preventDefault();
            if (open && this.active >= 0) this.add(this.items[this.active]);
            else this.commit();
            this.input.value = '';
            this.active = -1;
            this.refresh();
        } else if (e.key === 'Backspace' && !this.input.value && this.tags.length) {
            this.remove(this.tags.length - 1);
        } else if (e.key === 'Escape' && open) {
            e.stopPropagation(); this.list.hidden = true;
        }
    }

    render() {
        this.chips.innerHTML = this.tags.map((t, i) =>
            `<span class="chip">${esc(t)}<button type="button" data-i="${i}" tabindex="-1" title="Remove">×</button></span>`).join('');
    }
}
