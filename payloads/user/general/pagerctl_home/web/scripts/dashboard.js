/* ========================================
   Dashboard Tab - Pager system info
   ======================================== */
'use strict';

var DashboardTab = {
    init() {
        var panel = document.getElementById('tab-dashboard');
        panel.innerHTML =
            '<div class="card">' +
            '<h3>System</h3>' +
            '<div class="stat"><div class="lbl">CPU</div><div class="val" id="d-cpu">-</div></div>' +
            '<div class="stat"><div class="lbl">Memory</div><div class="val" id="d-mem">-</div></div>' +
            '<div class="stat"><div class="lbl">Temp</div><div class="val" id="d-temp">-</div></div>' +
            '<div class="stat"><div class="lbl">Disk</div><div class="val" id="d-disk">-</div></div>' +
            '<div class="stat"><div class="lbl">Uptime</div><div class="val" id="d-up">-</div></div>' +
            '<div class="stat"><div class="lbl">Procs</div><div class="val" id="d-procs">-</div></div>' +
            '<div class="stat"><div class="lbl">Battery</div><div class="val" id="d-battery">-</div></div>' +
            '</div>' +
            '<div class="card">' +
            '<h3>Identity</h3>' +
            '<div class="setting-row"><span>Hostname</span><span id="d-host">-</span></div>' +
            '<div class="setting-row"><span>Kernel</span><span id="d-kernel">-</span></div>' +
            '</div>' +
            '<div class="card">' +
            '<h3>Network</h3>' +
            '<div id="d-ifaces"><div class="text-muted">-</div></div>' +
            '</div>' +
            '<div class="card">' +
            '<h3>USB</h3>' +
            '<div id="d-usb"><div class="text-muted">-</div></div>' +
            '</div>';
    },

    activate() {
        App.startPolling('dashboard', () => this.refresh(), 2000);
    },

    deactivate() {},

    async refresh() {
        try {
            var s = await App.api('/api/sysinfo');
            this.setTxt('d-cpu', s.cpu);
            this.setTxt('d-mem', s.mem);
            this.setTxt('d-temp', s.temp);
            this.setTxt('d-disk', s.disk);
            this.setTxt('d-up', s.uptime);
            this.setTxt('d-procs', s.procs);
            this.setTxt('d-battery', s.battery);
            this.setTxt('d-host', s.hostname);
            this.setTxt('d-kernel', s.kernel);

            var ifDiv = document.getElementById('d-ifaces');
            var ifaces = s.interfaces || [];
            if (!ifaces.length) {
                ifDiv.innerHTML = '<div class="text-muted">No interfaces</div>';
            } else {
                ifDiv.innerHTML = ifaces.map(function(i) {
                    return '<div class="setting-row"><span>' + i[0] + '</span><span>' + i[1] + '</span></div>';
                }).join('');
            }

            var usbDiv = document.getElementById('d-usb');
            var usb = s.usb || [];
            if (!usb.length) {
                usbDiv.innerHTML = '<div class="text-muted">No external USB devices</div>';
            } else {
                usbDiv.innerHTML = usb.map(function(d) {
                    return '<div class="setting-row"><span>' + d + '</span></div>';
                }).join('');
            }
        } catch (e) {
            // silent retry
        }
    },

    setTxt(id, val) {
        var el = document.getElementById(id);
        if (el) el.textContent = val != null ? val : '-';
    }
};

App.registerTab('dashboard', DashboardTab);
