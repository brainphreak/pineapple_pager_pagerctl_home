/* ========================================
   Display Tab - Pager LCD Mirror (Live Framebuffer)
   ========================================
   Always renders directly in the pager's visible orientation
   (landscape when rotation=270) so canvas saves / right-click
   download give the user what they see instead of the raw
   portrait memory layout. */
'use strict';

var DisplayTab = {
    canvas: null,
    ctx: null,
    webDelay: 500,
    scale: 1,
    fbWidth: 222,        // framebuffer memory width (portrait)
    fbHeight: 480,       // framebuffer memory height (portrait)
    rotation: 270,       // 270 = landscape (CCW rotate for display)
    displayW: 480,       // what we actually render on canvas
    displayH: 222,
    refreshing: false,

    init() {
        var panel = document.getElementById('tab-display');
        panel.innerHTML = '<div class="loki-panel">' +
            '<div class="lcd-frame" id="lcd-frame">' +
            '<canvas id="lcd-canvas" width="480" height="222" class="lcd-img"></canvas>' +
            '</div>' +
            '<div class="lcd-controls">' +
            '<button class="btn btn-sm" id="lcd-zoom-out">-</button>' +
            '<span class="text-muted" id="lcd-zoom-label">100%</span>' +
            '<button class="btn btn-sm" id="lcd-zoom-in">+</button>' +
            '<button class="btn btn-sm" id="lcd-reset">Reset</button>' +
            '<button class="btn btn-sm btn-gold" id="lcd-refresh">Refresh</button>' +
            '<button class="btn btn-sm" id="lcd-download">Download PNG</button>' +
            '</div>' +
            '<div class="ctrl-container">' +
            '<div class="ctrl-layout">' +
            '<button class="btn ctrl-btn ctrl-b" data-btn="b">B</button>' +
            '<button class="btn ctrl-btn ctrl-a" data-btn="a">A</button>' +
            '<div class="ctrl-grid">' +
            '<div></div>' +
            '<button class="btn ctrl-btn" data-btn="up">&#9650;</button>' +
            '<div></div>' +
            '<button class="btn ctrl-btn" data-btn="left">&#9664;</button>' +
            '<div></div>' +
            '<button class="btn ctrl-btn" data-btn="right">&#9654;</button>' +
            '<div></div>' +
            '<button class="btn ctrl-btn" data-btn="down">&#9660;</button>' +
            '<div></div>' +
            '</div>' +
            '</div>' +
            '<div class="ctrl-row">' +
            '<button class="btn btn-sm" data-btn="power">Power</button>' +
            '</div>' +
            '</div>' +
            '<style>' +
            '.loki-panel { display: flex; flex-direction: column; align-items: center; gap: 12px; }' +
            '.ctrl-container { display: flex; flex-direction: column; align-items: center; gap: 10px; margin-top: 8px; }' +
            '.ctrl-layout { display: flex; align-items: center; gap: 14px; }' +
            '.ctrl-grid { display: grid; grid-template-columns: repeat(3, 56px); gap: 6px; }' +
            '.ctrl-btn { aspect-ratio: 1; font-size: 18px; min-width: 56px; min-height: 56px; }' +
            '.ctrl-a, .ctrl-b { font-weight: bold; font-size: 22px; min-width: 64px; min-height: 64px; }' +
            '.ctrl-row { display: flex; gap: 8px; }' +
            '</style></div>';

        this.canvas = document.getElementById('lcd-canvas');
        this.ctx = this.canvas.getContext('2d');

        document.getElementById('lcd-zoom-in').addEventListener('click', () => this.zoom(0.25));
        document.getElementById('lcd-zoom-out').addEventListener('click', () => this.zoom(-0.25));
        document.getElementById('lcd-reset').addEventListener('click', () => {
            this.scale = 1;
            this.applyZoom();
        });
        document.getElementById('lcd-refresh').addEventListener('click', () => this.refresh());
        document.getElementById('lcd-download').addEventListener('click', () => this.download());

        // Button panel handlers
        var self = this;
        panel.querySelectorAll('[data-btn]').forEach(function(b) {
            b.addEventListener('click', function() {
                self.press(b.dataset.btn);
            });
        });

        // Keyboard shortcuts while Display tab is active
        document.addEventListener('keydown', function(e) {
            if (App.activeTab !== 'display') return;
            // Skip when focused in an input (e.g., terminal on another tab)
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
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

        var frame = document.getElementById('lcd-frame');
        var self = this;
        frame.addEventListener('wheel', function(e) {
            e.preventDefault();
            self.zoom(e.deltaY < 0 ? 0.1 : -0.1);
        }, { passive: false });

        var lastDist = 0;
        frame.addEventListener('touchstart', function(e) {
            if (e.touches.length === 2) {
                lastDist = Math.hypot(
                    e.touches[0].clientX - e.touches[1].clientX,
                    e.touches[0].clientY - e.touches[1].clientY
                );
            }
        });
        frame.addEventListener('touchmove', function(e) {
            if (e.touches.length === 2) {
                e.preventDefault();
                var dist = Math.hypot(
                    e.touches[0].clientX - e.touches[1].clientX,
                    e.touches[0].clientY - e.touches[1].clientY
                );
                if (lastDist) {
                    var delta = (dist - lastDist) / 100;
                    self.zoom(delta);
                }
                lastDist = dist;
            }
        }, { passive: false });
    },

    activate() {
        App.startPolling('display', () => this.refresh(), this.webDelay);
    },

    deactivate() {
        App.stopPolling('display');
    },

    async refresh() {
        if (this.refreshing) return;
        this.refreshing = true;
        try {
            var resp = await fetch('/screen.png?t=' + Date.now());
            if (!resp.ok) throw new Error('HTTP ' + resp.status);

            var ct = resp.headers.get('content-type') || '';
            if (ct.includes('octet-stream')) {
                var buf = await resp.arrayBuffer();
                var header = new Uint16Array(buf, 0, 3);
                var fw = header[0];
                var fh = header[1];
                var rot = header[2];

                // Adapt canvas size to the current orientation. For 270°
                // (landscape) the canvas is fh x fw (480 x 222); for 0°
                // it's fw x fh.
                var needW = (rot === 270) ? fh : fw;
                var needH = (rot === 270) ? fw : fh;
                if (fw !== this.fbWidth || fh !== this.fbHeight || rot !== this.rotation) {
                    this.fbWidth = fw;
                    this.fbHeight = fh;
                    this.rotation = rot;
                    this.displayW = needW;
                    this.displayH = needH;
                    this.canvas.width = needW;
                    this.canvas.height = needH;
                    this.applyZoom();
                }

                this.renderRGB565(buf, 6, rot);
            } else {
                // PNG fallback (e.g., dev without framebuffer)
                var blob = await resp.blob();
                var img = new Image();
                var self = this;
                img.onload = function() {
                    self.ctx.drawImage(img, 0, 0);
                    URL.revokeObjectURL(img.src);
                };
                img.src = URL.createObjectURL(blob);
            }
        } catch (e) {
            // silent retry
        } finally {
            this.refreshing = false;
        }
    },

    /* Render raw RGB565 pixels into the canvas, rotating at the pixel
       level so the canvas buffer itself is landscape — no CSS transform
       needed. That means canvas.toDataURL() and right-click save give
       the user an image matching what they see. */
    renderRGB565(buffer, offset, rotation) {
        var pixels = new Uint16Array(buffer, offset || 0);
        var pw = this.fbWidth;
        var ph = this.fbHeight;
        var lw = this.canvas.width;
        var lh = this.canvas.height;
        var imageData = this.ctx.createImageData(lw, lh);
        var data = imageData.data;

        if (rotation === 270) {
            // 90° CCW: portrait (x, y) → landscape (y, pw - 1 - x)
            for (var y = 0; y < ph; y++) {
                for (var x = 0; x < pw; x++) {
                    var px = pixels[y * pw + x];
                    var lx = y;
                    var ly = pw - 1 - x;
                    var idx = (ly * lw + lx) * 4;
                    data[idx]     = ((px >> 11) & 0x1F) << 3;
                    data[idx + 1] = ((px >> 5) & 0x3F) << 2;
                    data[idx + 2] = (px & 0x1F) << 3;
                    data[idx + 3] = 255;
                }
            }
        } else {
            // No rotation
            for (var i = 0; i < pixels.length; i++) {
                var p = pixels[i];
                var j = i * 4;
                data[j]     = ((p >> 11) & 0x1F) << 3;
                data[j + 1] = ((p >> 5) & 0x3F) << 2;
                data[j + 2] = (p & 0x1F) << 3;
                data[j + 3] = 255;
            }
        }

        this.ctx.putImageData(imageData, 0, 0);
    },

    zoom(delta) {
        this.scale = Math.max(0.5, Math.min(4, this.scale + delta));
        this.applyZoom();
    },

    applyZoom() {
        var sw = Math.round(this.displayW * this.scale);
        var sh = Math.round(this.displayH * this.scale);
        if (this.canvas) {
            this.canvas.style.transform = '';
            this.canvas.style.transformOrigin = '';
            this.canvas.style.width = sw + 'px';
            this.canvas.style.height = sh + 'px';
        }
        var frame = document.getElementById('lcd-frame');
        if (frame) {
            frame.style.width = Math.round(sw + 20) + 'px';
            frame.style.height = Math.round(sh + 20) + 'px';
        }
        var label = document.getElementById('lcd-zoom-label');
        if (label) label.textContent = Math.round(this.scale * 100) + '%';
    },

    download() {
        if (!this.canvas) return;
        var ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
        var url = this.canvas.toDataURL('image/png');
        var a = document.createElement('a');
        a.href = url;
        a.download = 'pager-' + ts + '.png';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    },

    async press(name) {
        try {
            await App.post('/api/button/' + name, {});
        } catch (e) {
            App.toast('Button failed: ' + name, 'error');
        }
    }
};

App.registerTab('display', DisplayTab);
