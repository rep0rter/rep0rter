"use strict";
// Progressive local search: metadata stays inside removable report articles.
(() => {
    let dispose = () => { };
    const initialize = () => {
        dispose();
        const removers = [];
        const listen = (target, type, handler) => {
            if (!target)
                return;
            target.addEventListener(type, handler);
            removers.push(() => target.removeEventListener(type, handler));
        };
        dispose = () => { removers.splice(0).forEach(remove => remove()); };
        const form = document.querySelector('[data-search-form]');
        if (!form)
            return;
        // index.html only marks the form with data-search-form on non-detail pages,
        // and renders these four controls under the same condition, so reaching
        // this point means they exist.
        const input = form.querySelector('input');
        const clear = document.querySelector('[data-search-clear]');
        const status = document.querySelector('#search-status');
        const empty = document.querySelector('[data-no-results]');
        const filters = document.querySelector('[data-filters]');
        const countBadge = document.querySelector('[data-filter-count]');
        const error = document.querySelector('[data-filter-error]');
        const filterNames = ['period', 'from', 'to', 'topic', 'author', 'source', 'sort'];
        const fields = Object.fromEntries(filterNames
            .map(name => [name, filters?.querySelector(`[data-filter="${name}"]`) ?? null]));
        const defaults = { period: 'all', from: '', to: '', topic: '', author: '', source: '', sort: 'newest' };
        const normalize = (value) => (value || '').normalize('NFKC').toLocaleLowerCase(document.documentElement.lang);
        const articles = [...document.querySelectorAll('article[data-search]')].map((element, order) => {
            let topics = [];
            try {
                topics = JSON.parse(element.dataset.topics || '[]');
            }
            catch { /* Ignore malformed optional metadata. */ }
            return {
                element, order, text: normalize(element.dataset.search),
                date: element.dataset.date || '', timestamp: Number(element.dataset.timestamp) || 0,
                topics: Array.isArray(topics)
                    ? topics.filter(topic => topic?.id && topic?.label) : [],
            };
        });
        if (!articles.length)
            return;
        const days = [...document.querySelectorAll('.day')].map((day, order) => {
            const stories = articles.filter(article => day.contains ? day.contains(article.element) : [...day.querySelectorAll('article')].includes(article.element));
            return { day, order, stories, grid: day.querySelector('.story-grid'), timestamp: Math.max(0, ...stories.map(article => article.timestamp)) };
        });
        let previousSort = null;
        const dateFormatter = new Intl.DateTimeFormat('en', { timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit' });
        const validDate = (value) => /^\d{4}-\d{2}-\d{2}$/.test(value) &&
            !Number.isNaN(Date.parse(`${value}T00:00:00Z`)) &&
            new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
        const values = { topic: new Map(), author: new Map(), source: new Map() };
        articles.forEach(({ element, topics }) => {
            topics.forEach(({ id, label }) => values.topic.set(String(id), String(label)));
            ['author', 'source'].forEach(name => {
                const id = element.dataset[name];
                if (id)
                    values[name].set(id, element.dataset[`${name}Label`] || id);
            });
        });
        // Distinct accounts can share a display name; keep their choices distinguishable.
        const disambiguate = (options, suffix) => {
            const counts = new Map();
            options.forEach((label, id) => counts.set(label, [...(counts.get(label) || []), id]));
            counts.forEach(ids => ids.sort());
            options.forEach((label, id) => {
                const ids = counts.get(label);
                if (ids.length > 1)
                    options.set(id, `${label} · ${suffix(id, ids.indexOf(id) + 1)}`);
            });
        };
        disambiguate(values.source, (id, number) => number);
        disambiguate(values.author, (id, number) => [...new Set(articles
                .filter(({ element }) => element.dataset.author === id)
                .map(({ element }) => {
                const source = element.dataset.source;
                return (source && values.source.get(source)) || element.dataset.sourceLabel || source;
            })
                .filter(Boolean))].sort().join(' / ') || number);
        // Even within one source two accounts may have the same display name.
        disambiguate(values.author, (id, number) => number);
        Object.entries(values).forEach(([name, options]) => {
            const field = fields[name];
            if (!field)
                return;
            [...options].sort((a, b) => a[1].localeCompare(b[1], document.documentElement.lang))
                .forEach(([value, label]) => {
                const option = document.createElement('option');
                option.value = value;
                option.textContent = label;
                field.append(option);
            });
        });
        const getState = () => Object.fromEntries(Object.entries(defaults)
            .map(([name, fallback]) => [name, fields[name]?.value || fallback]));
        const setState = (state) => {
            Object.entries(defaults).forEach(([name, fallback]) => {
                const field = fields[name];
                if (field)
                    field.value = state[name] || fallback;
            });
        };
        const readURL = () => {
            const params = new URL(window.location.href).searchParams;
            input.value = params.get('q') || '';
            const state = {};
            filterNames.forEach(name => { state[name] = params.get(name) || defaults[name]; });
            if (!['all', '7', '30', '90', 'custom'].includes(state.period))
                state.period = 'all';
            if (!['newest', 'oldest'].includes(state.sort))
                state.sort = 'newest';
            ['from', 'to'].forEach(name => { if (!validDate(state[name]))
                state[name] = ''; });
            ['topic', 'author', 'source'].forEach(name => {
                if (!values[name].has(state[name]))
                    state[name] = '';
            });
            setState(state);
        };
        const writeURL = (state) => {
            const url = new URL(window.location.href);
            const query = input.value.trim();
            if (query)
                url.searchParams.set('q', query);
            else
                url.searchParams.delete('q');
            Object.entries(defaults).forEach(([name, fallback]) => {
                const value = state[name];
                if (value && value !== fallback && (!['from', 'to'].includes(name) || state.period === 'custom')) {
                    url.searchParams.set(name, value);
                }
                else
                    url.searchParams.delete(name);
            });
            if (url.href !== window.location.href)
                window.history.replaceState(window.history.state, '', url.href);
        };
        const update = (persist = true) => {
            const state = getState();
            const terms = normalize(input.value.trim()).split(/\s+/).filter(Boolean);
            const custom = state.period === 'custom';
            const customDates = filters?.querySelector('[data-custom-dates]');
            if (customDates)
                customDates.hidden = !custom;
            const invalid = custom && state.from && state.to && state.from > state.to;
            ['from', 'to'].forEach(name => {
                const field = fields[name];
                if (field) {
                    field.disabled = !custom;
                    field.setAttribute('aria-invalid', invalid ? 'true' : 'false');
                }
            });
            if (error)
                error.hidden = !invalid;
            // Calendar days are based on the publication timezone, including today.
            const dateParts = Object.fromEntries(dateFormatter.formatToParts(new Date()).map(part => [part.type, part.value]));
            const today = `${dateParts.year}-${dateParts.month}-${dateParts.day}`;
            const earliest = ['7', '30', '90'].includes(state.period)
                ? new Date(Date.parse(`${today}T00:00:00Z`) - (Number(state.period) - 1) * 86400000).toISOString().slice(0, 10) : '';
            let count = 0;
            articles.forEach(({ element, text, date, topics }) => {
                const matches = !invalid && terms.every(term => text.includes(term)) &&
                    (!state.topic || topics.some(topic => String(topic.id) === state.topic)) &&
                    (!state.author || element.dataset.author === state.author) &&
                    (!state.source || element.dataset.source === state.source) &&
                    (!earliest || (date >= earliest && date <= today)) &&
                    (!custom || ((!state.from || date >= state.from) && (!state.to || date <= state.to)));
                element.hidden = !matches;
                if (matches)
                    count += 1;
            });
            const direction = state.sort === 'oldest' ? 1 : -1;
            const compare = (a, b) => direction * (a.timestamp - b.timestamp ||
                (Number(a.element.dataset.postId) || a.order) - (Number(b.element.dataset.postId) || b.order));
            days.forEach(({ day, stories, grid }) => {
                if (previousSort !== state.sort && grid)
                    stories.sort(compare).forEach(article => grid.append(article.element));
                const visible = stories.filter(article => !article.element.hidden).length;
                day.hidden = visible === 0;
                day.querySelector('.day-count').textContent = String(visible);
            });
            if (previousSort !== state.sort) {
                [...days].sort((a, b) => direction * (a.timestamp - b.timestamp) || a.order - b.order)
                    .forEach(({ day }) => { if (day.parentElement)
                    day.parentElement.append(day); });
                previousSort = state.sort;
            }
            const summarised = ['period', 'topic', 'author', 'source'];
            const activeCount = summarised.filter(name => state[name] !== defaults[name]).length + (state.sort === 'oldest' ? 1 : 0);
            if (countBadge) {
                countBadge.textContent = String(activeCount);
                countBadge.hidden = !activeCount;
            }
            document.documentElement.dataset.searchActive =
                String(Boolean(terms.length || summarised.some(name => state[name] !== defaults[name])));
            clear.hidden = !input.value;
            status.hidden = !terms.length && !activeCount;
            status.textContent = status.dataset.resultTemplate.replace('{count}', String(count));
            empty.hidden = count !== 0;
            if (persist)
                writeURL(state);
        };
        const clearQuery = () => { input.value = ''; update(); input.focus(); };
        const reset = () => { input.value = ''; setState(defaults); update(); input.focus(); };
        form.hidden = false;
        if (filters)
            filters.hidden = false;
        readURL();
        if (filters && Object.entries(getState())
            .some(([name, value]) => value !== defaults[name]))
            filters.open = true;
        update(false);
        listen(form, 'submit', event => {
            event.preventDefault();
            update();
            document.querySelector('#news')?.scrollIntoView({ block: 'start', behavior: 'auto' });
        });
        listen(input, 'input', () => update());
        listen(input, 'keydown', event => { if (event.key === 'Escape')
            clearQuery(); });
        listen(clear, 'click', clearQuery);
        Object.values(fields).forEach(field => listen(field, 'change', () => update()));
        listen(document.querySelector('[data-filters-reset]'), 'click', reset);
        listen(document.querySelector('[data-search-reset]'), 'click', reset);
        listen(window, 'popstate', () => { readURL(); update(false); });
    };
    document.addEventListener('rep0rter:before-language', () => dispose());
    document.addEventListener('rep0rter:language-applied', initialize);
    initialize();
})();
