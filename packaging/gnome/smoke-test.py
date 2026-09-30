#!/usr/bin/env python3
"""Exercise the extension in a disposable GNOME 42 Wayland session."""

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time


UUID = 'linux-wallpaperengine@almamu.github.io'
SCHEMA = 'org.gnome.shell.extensions.linux-wallpaperengine'
SOURCE = Path(__file__).resolve().parent


def wait_for(predicate):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError('Timed out waiting for a GNOME/renderer state transition')


def run_test(args):
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    from PIL import Image, ImageChops, ImageStat

    extension_dir = Path(os.environ['XDG_DATA_HOME']) / 'gnome-shell/extensions' / UUID

    def setting(key, value):
        subprocess.run(['gsettings', '--schemadir', str(extension_dir / 'schemas'),
                        'set', SCHEMA, key, value], check=True)

    def extension(method):
        return subprocess.check_output([
            'gdbus', 'call', '--session', '--dest', 'org.gnome.Shell',
            '--object-path', '/org/gnome/Shell',
            '--method', 'org.gnome.Shell.Extensions.' + method, UUID], text=True)

    def renderer():
        returncode = shell.poll()
        if returncode is not None:
            raise AssertionError('gnome-shell exited with code %s' % returncode)
        children = Path('/proc/%d/task/%d/children' % (shell.pid, shell.pid))
        if not children.exists():
            return None
        for child in children.read_text().split():
            try:
                argv = Path('/proc/%s/cmdline' % child).read_bytes().split(b'\0')
            except FileNotFoundError:
                continue
            if str(args.wallpaper).encode() in argv:
                return int(child)
        return None

    def usage(pid):
        # Fields after comm start with state (field 3); comm can contain spaces.
        fields = Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()
        return fields[0], int(fields[11]) + int(fields[12])

    def wait_until_paused(pid, paused):
        wait_for(lambda: (usage(pid)[0] == 'T') == paused)

    def wait_until_exited(pid):
        wait_for(lambda: not Path('/proc/%d' % pid).exists())

    setting('engine-path', json.dumps(str(args.engine)))
    setting('wallpaper', json.dumps(str(args.wallpaper)))
    setting('assets-dir', json.dumps(str(args.assets_dir)) if args.assets_dir else "''")
    subprocess.run(['gsettings', 'set', 'org.gnome.shell', 'enabled-extensions',
                    "['%s']" % UUID], check=True)
    subprocess.run(['gsettings', 'set', 'org.gnome.shell', 'disabled-extensions', '[]'], check=True)

    with (args.output / 'gnome-shell.log').open('w') as log:
        shell = subprocess.Popen([
            'gnome-shell', '--headless', '--wayland', '--no-x11',
            '--virtual-monitor', '2560x1440', '--wayland-display=lwe-smoke'],
            stdout=log, stderr=subprocess.STDOUT)
        try:
            pid = wait_for(renderer)
            wait_for(lambda: 'VO: [libmpv]' in (args.output / 'gnome-shell.log').read_text())
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            connection.call_sync(
                'org.gnome.Shell', '/org/gnome/Shell', 'org.freedesktop.DBus.Properties', 'Set',
                GLib.Variant('(ssv)', ('org.gnome.Shell', 'OverviewActive', GLib.Variant('b', False))),
                None, Gio.DBusCallFlags.NONE, 5000, None)
            assert "'error': <''>" in extension('GetExtensionInfo')
            reply = connection.call_sync(
                'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                'RequestName', GLib.Variant('(su)', ('org.gnome.Screenshot', 0)),
                None, Gio.DBusCallFlags.NONE, 5000, None)
            assert reply.unpack()[0] == 1
            frames = []
            for index in range(2):
                time.sleep(5)
                path = args.output / ('background-%d.png' % index)
                reply = connection.call_sync(
                    'org.gnome.Shell.Screenshot', '/org/gnome/Shell/Screenshot',
                    'org.gnome.Shell.Screenshot', 'Screenshot',
                    GLib.Variant('(bbs)', (False, False, str(path))),
                    None, Gio.DBusCallFlags.NONE, 15000, None)
                assert reply.unpack()[0]
                frame = Image.open(path).convert('RGB').crop((200, 200, 2300, 1300))
                assert max(ImageStat.Stat(frame).stddev) > 10
                frames.append(frame)
            difference = ImageStat.Stat(ImageChops.difference(*frames)).mean
            assert max(difference) > 1, difference
            print('PASS: composited background frames change', flush=True)

            setting('paused', 'true')
            wait_for(lambda: usage(pid)[0] == 'T')
            ticks = usage(pid)[1]
            time.sleep(2)
            assert usage(pid)[1] == ticks
            setting('paused', 'false')
            wait_for(lambda: usage(pid)[0] != 'T' and usage(pid)[1] > ticks)
            print('PASS: pause stops CPU work and resume restarts it', flush=True)

            fullscreen = subprocess.Popen([sys.executable, '-c', """
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk
window = Gtk.Window(title='Wallpaper fullscreen test')
window.fullscreen()
window.show_all()
Gtk.main()
"""])
            try:
                wait_for(lambda: usage(pid)[0] == 'T')
            finally:
                fullscreen.terminate()
                fullscreen.wait(timeout=5)
            wait_for(lambda: usage(pid)[0] != 'T')
            print('PASS: fullscreen pause and resume', flush=True)

            old_pid = pid
            setting('fps', '25')
            pid = wait_for(lambda: renderer() if renderer() != old_pid else None)
            wait_for(lambda: not Path('/proc/%d' % old_pid).exists())
            assert b'25' in Path('/proc/%d/cmdline' % pid).read_bytes().split(b'\0')
            print('PASS: settings change restarts the renderer with new arguments', flush=True)

            for paused in (False, True):
                setting('paused', 'true' if paused else 'false')
                wait_until_paused(pid, paused)
                assert 'true' in extension('DisableExtension')
                wait_until_exited(pid)
                setting('paused', 'false')
                assert 'true' in extension('EnableExtension')
                pid = wait_for(renderer)
            assert 'true' in extension('DisableExtension')
            wait_for(lambda: not Path('/proc/%d' % pid).exists())
            print('PASS: running/paused child cleanup and re-enable', flush=True)
            (args.output / 'result.json').write_text(json.dumps({
                'passed': True, 'background_difference_rgb': difference,
                'environment': 'isolated headless GNOME 42; functional test only',
            }, indent=2) + '\n')
        finally:
            if shell.poll() is None:
                try:
                    extension('DisableExtension')
                finally:
                    # A renderer stuck during startup may not handle SIGTERM. Only
                    # kill a child still owned by this test's compositor.
                    deadline = time.monotonic() + 5
                    while renderer() and time.monotonic() < deadline:
                        time.sleep(0.1)
                    child = renderer()
                    if child:
                        try:
                            os.kill(child, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    shell.terminate()
                    try:
                        shell.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        shell.kill()
                        shell.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--wallpaper', type=Path, required=True)
    parser.add_argument('--assets-dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--isolated', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.engine = args.engine.resolve(strict=True)
    args.wallpaper = args.wallpaper.resolve(strict=True)
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.assets_dir:
        args.assets_dir = args.assets_dir.resolve(strict=True)
    if args.isolated:
        run_test(args)
        return

    with tempfile.TemporaryDirectory(prefix='lwe-gnome-test-') as directory:
        root = Path(directory)
        env = os.environ.copy()
        for key, name in [('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'),
                          ('XDG_DATA_HOME', 'data'), ('XDG_RUNTIME_DIR', 'runtime')]:
            path = root / name
            path.mkdir(mode=0o700)
            env[key] = str(path)
        extension = root / 'data/gnome-shell/extensions' / UUID
        (extension / 'schemas').mkdir(parents=True)
        for name in ('extension.js', 'metadata.json'):
            (extension / name).symlink_to(SOURCE / name)
        schema = SCHEMA + '.gschema.xml'
        (extension / 'schemas' / schema).symlink_to(SOURCE / 'schemas' / schema)
        subprocess.run(['glib-compile-schemas', '--strict', str(extension / 'schemas')], check=True)
        env.update(GSETTINGS_BACKEND='keyfile', GNOME_SHELL_SESSION_MODE='gnome',
                   WAYLAND_DISPLAY='lwe-smoke', GDK_BACKEND='wayland',
                   SDL_AUDIODRIVER='dummy')
        env.pop('DISPLAY', None)
        command = ['dbus-run-session', '--', sys.executable, str(Path(__file__).resolve()),
                   '--isolated', '--engine', str(args.engine), '--wallpaper', str(args.wallpaper),
                   '--output', str(args.output)]
        if args.assets_dir:
            command.extend(['--assets-dir', str(args.assets_dir)])
        try:
            subprocess.run(command, env=env, check=True)
        finally:
            # Portal/GVfs mounts can outlive the private session bus. Detach only
            # mounts inside this test's runtime directory before removing it.
            prefix = str(root / 'runtime') + '/'
            mounts = [line.split()[4] for line in Path('/proc/self/mountinfo').read_text().splitlines()
                      if line.split()[4].startswith(prefix)]
            for mount in sorted(mounts, key=len, reverse=True):
                unmount = shutil.which('fusermount3') or shutil.which('fusermount')
                subprocess.run([unmount, '-uz', mount], check=True)


if __name__ == '__main__':
    main()
