/* ========================================
   Pagerctl Home - SPA Core (adapted from Loki/Bjorn)
   ======================================== */
'use strict';

const App = {
    activeTab: null,
    tabs: {},
    pollTimers: {},

    init() {
        document.querySelectorAll('.nav-item').forEach(el => {
            el.addEventListener('click', e => {
                e.preventDefault();
                this.switchTab(el.dataset.tab);
            });
        });

        window.addEventListener('hashchange', () => {
            var tab = location.hash.slice(1);
            if (tab && this.tabs[tab]) this.switchTab(tab);
        });

        // Initialize registered tabs
        Object.keys(this.tabs).forEach(id => {
            if (this.tabs[id].init) this.tabs[id].init();
        });

        // Restore last tab or use hash
        var hash = location.hash.slice(1);
        var saved = localStorage.getItem('pagerctl_active_tab');
        var initial = (hash && this.tabs[hash]) ? hash
                      : (saved && this.tabs[saved]) ? saved
                      : 'dashboard';
        this.switchTab(initial);
    },

    registerTab(id, module) {
        this.tabs[id] = module;
    },

    switchTab(id) {
        if (!this.tabs[id]) return;
        if (this.activeTab === id) return;

        if (this.activeTab && this.tabs[this.activeTab]) {
            const prev = this.tabs[this.activeTab];
            if (prev.deactivate) prev.deactivate();
            this.stopPolling(this.activeTab);
            const prevPanel = document.getElementById('tab-' + this.activeTab);
            if (prevPanel) prevPanel.classList.remove('active');
        }

        document.querySelectorAll('.nav-item').forEach(el => {
            el.classList.toggle('active', el.dataset.tab === id);
        });

        this.activeTab = id;
        const panel = document.getElementById('tab-' + id);
        if (panel) panel.classList.add('active');

        const tab = this.tabs[id];
        if (tab.activate) tab.activate();

        if (location.hash !== '#' + id) {
            history.replaceState(null, '', '#' + id);
        }
        localStorage.setItem('pagerctl_active_tab', id);
    },

    startPolling(tabId, fn, interval) {
        this.stopPolling(tabId);
        fn();
        this.pollTimers[tabId] = setInterval(() => {
            if (this.activeTab === tabId) fn();
        }, interval);
    },

    stopPolling(tabId) {
        if (this.pollTimers[tabId]) {
            clearInterval(this.pollTimers[tabId]);
            delete this.pollTimers[tabId];
        }
    },

    async api(url, opts) {
        try {
            const resp = await fetch(url, opts || {});
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const ct = resp.headers.get('content-type') || '';
            if (ct.includes('json')) return resp.json();
            return resp.text();
        } catch (e) {
            console.error('API error:', url, e);
            throw e;
        }
    },

    async post(url, data) {
        return this.api(url, {
            method: 'POST',
            headers: data ? { 'Content-Type': 'application/json' } : {},
            body: data ? JSON.stringify(data) : undefined
        });
    },

    toast(msg, type) {
        type = type || 'info';
        const container = document.getElementById('toast-container');
        if (!container) { console.log('[toast]', msg); return; }
        const el = document.createElement('div');
        el.className = 'toast toast-' + type;
        el.textContent = msg;
        container.appendChild(el);
        setTimeout(() => {
            el.classList.add('removing');
            setTimeout(() => el.remove(), 300);
        }, 3000);
    }
};

document.addEventListener('DOMContentLoaded', () => App.init());
