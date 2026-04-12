/* ========================================
   Control Tab - Remote button input
   ======================================== */
'use strict';

var ControlTab = {
    init() {
        var panel = document.getElementById('tab-control');
        panel.innerHTML =
            '<div class="card">' +
            '<h3>Remote Control</h3>' +
            '<p class="text-muted">Click a button to press it on the pager.</p>' +
            '<div class="ctrl-grid">' +
            '<div></div>' +
            '<button class="btn ctrl-btn" data-btn="up">&#9650;</button>' +
            '<div></div>' +
            '<button class="btn ctrl-btn" data-btn="left">&#9664;</button>' +
            '<button class="btn ctrl-btn" data-btn="a">A</button>' +
            '<button class="btn ctrl-btn" data-btn="right">&#9654;</button>' +
            '<div></div>' +
            '<button class="btn ctrl-btn" data-btn="down">&#9660;</button>' +
            '<div></div>' +
            '</div>' +
            '<div class="ctrl-row">' +
            '<button class="btn btn-sm" data-btn="b">B (Back)</button>' +
            '<button class="btn btn-sm" data-btn="power">Power</button>' +
            '</div>' +
            '</div>' +
            '<style>' +
            '.ctrl-grid { display: grid; grid-template-columns: repeat(3, 60px); gap: 8px; margin: 16px 0; }' +
            '.ctrl-btn { aspect-ratio: 1; font-size: 18px; }' +
            '.ctrl-row { display: flex; gap: 8px; }' +
            '</style>';

        var self = this;
        panel.querySelectorAll('[data-btn]').forEach(function(b) {
            b.addEventListener('click', function() {
                self.press(b.dataset.btn);
            });
        });

        // Keyboard shortcuts while tab is active
        document.addEventListener('keydown', function(e) {
            if (App.activeTab !== 'control') return;
            var map = {
                'ArrowUp': 'up', 'ArrowDown': 'down',
                'ArrowLeft': 'left', 'ArrowRight': 'right',
                'Enter': 'a', 'a': 'a', 'A': 'a',
                'Escape': 'b', 'b': 'b', 'B': 'b',
                'p': 'power', 'P': 'power'
            };
            var btn = map[e.key];
            if (btn) {
                e.preventDefault();
                self.press(btn);
            }
        });
    },

    activate() {},
    deactivate() {},

    async press(name) {
        try {
            await App.post('/api/button/' + name, {});
        } catch (e) {
            App.toast('Button failed: ' + name, 'error');
        }
    }
};

App.registerTab('control', ControlTab);
