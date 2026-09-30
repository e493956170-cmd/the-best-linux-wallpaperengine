/* exported init */
'use strict';

const {Clutter, Gio, GLib, Meta} = imports.gi;
const Main = imports.ui.main;
const ExtensionUtils = imports.misc.extensionUtils;

class WallpaperExtension {
    enable() {
        if (!Meta.is_wayland_compositor())
            throw new Error('The wallpaper extension requires a Wayland session');

        this._enabled = true;
        this._signals = [];
        this._windowSignals = [];
        this._settings = ExtensionUtils.getSettings();
        this._connect(global.window_manager, 'map', (_wm, actor) => this._attach(actor));
        this._connect(global.display, 'in-fullscreen-changed', () => this._updatePause());
        this._connect(Main.layoutManager, 'monitors-changed', () => this._restart());
        this._connect(this._settings, 'changed', (_settings, key) => {
            if (key === 'paused')
                this._updatePause();
            else
                this._restart();
        });
        if (Main.layoutManager._startingUp)
            this._connect(Main.layoutManager, 'startup-complete', () => this._launch());
        else
            this._launch();
    }

    _connect(object, signal, callback) {
        this._signals.push([object, object.connect(signal, callback)]);
    }

    _launch() {
        if (!this._enabled || this._process || !Main.layoutManager.primaryMonitor)
            return;

        const wallpaper = this._settings.get_string('wallpaper');
        if (!wallpaper) {
            log('linux-wallpaperengine: configure a wallpaper before starting playback');
            return;
        }
        const executable = GLib.find_program_in_path(this._settings.get_string('engine-path'));
        if (!executable) {
            log('linux-wallpaperengine: engine-path does not point to an executable');
            return;
        }
        const monitor = Main.layoutManager.primaryMonitor;
        const argv = [
            executable, '--window', `0x0x${monitor.width}x${monitor.height}`,
            '--fps', String(this._settings.get_uint('fps')),
            '--silent', '--noautomute', '--disable-mouse', '--no-fullscreen-pause',
        ];
        const assets = this._settings.get_string('assets-dir');
        if (assets)
            argv.push('--assets-dir', assets);
        argv.push(wallpaper);

        const launcher = new Gio.SubprocessLauncher({flags: Gio.SubprocessFlags.NONE});
        try {
            this._client = Meta.WaylandClient.new(launcher);
            this._process = this._client.spawnv(global.display, argv);
        } catch (error) {
            this._client = null;
            logError(error, 'linux-wallpaperengine: failed to start renderer');
            return;
        } finally {
            launcher.close();
        }
        this._paused = false;
        const process = this._process;
        // Keep the native client alive until its child has been reaped, even on disable.
        const client = this._client;
        process.wait_async(null, (source, result) => {
            source.wait_finish(result);
            if (this._process !== process || this._client !== client)
                return;
            this._process = null;
            this._client = null;
            this._clearWindow();
            log('linux-wallpaperengine: renderer exited');
        });
    }

    _attach(actor) {
        const window = actor.meta_window;
        if (!this._client || !this._client.owns_window(window) || this._clone)
            return;

        const monitor = Main.layoutManager.primaryMonitor;
        this._window = window;
        this._client.hide_from_window_list(window);
        window.move_to_monitor(monitor.index);
        window.move_resize_frame(false, monitor.x, monitor.y, monitor.width, monitor.height);
        this._clone = new Clutter.Clone({
            source: actor, reactive: false,
            x: monitor.x, y: monitor.y, width: monitor.width, height: monitor.height,
        });
        Main.layoutManager._backgroundGroup.add_child(this._clone);
        // Clutter keeps the cloned surface painted while the source window is minimized.
        this._windowSignals.push([window, window.connect('notify::minimized', () => {
            if (this._enabled && !window.minimized)
                window.minimize();
        })]);
        this._windowSignals.push([window, window.connect('unmanaged', () => this._clearWindow())]);
        window.minimize();
        this._updatePause();
    }

    _updatePause() {
        if (!this._process || !this._clone || !Main.layoutManager.primaryMonitor)
            return;
        const paused = this._settings.get_boolean('paused') ||
            global.display.get_monitor_in_fullscreen(Main.layoutManager.primaryMonitor.index);
        if (paused !== this._paused) {
            this._process.send_signal(paused ? 19 : 18); // SIGSTOP / SIGCONT on Linux
            this._paused = paused;
        }
    }

    _clearWindow() {
        for (const [object, id] of this._windowSignals)
            object.disconnect(id);
        this._windowSignals = [];
        if (this._clone) {
            this._clone.destroy();
            this._clone = null;
        }
        this._window = null;
    }

    _stop() {
        const process = this._process;
        this._process = null;
        if (process) {
            if (this._paused)
                process.send_signal(18); // Let a stopped process handle SIGTERM.
            process.send_signal(15);
        }
        this._paused = false;
        this._clearWindow();
        this._client = null;
    }

    _restart() {
        if (!this._enabled || Main.layoutManager._startingUp)
            return;
        this._stop();
        this._launch();
    }

    disable() {
        this._enabled = false;
        for (const [object, id] of this._signals)
            object.disconnect(id);
        this._signals = [];
        this._stop();
        this._settings = null;
    }
}

function init() {
    return new WallpaperExtension();
}
