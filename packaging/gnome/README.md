# GNOME Wayland extension

GNOME does not provide the layer-shell protocol used by the engine's Wayland
background mode. This companion extension starts the engine in a native Wayland
window and places its surface in GNOME's background group.

Currently supported: **GNOME Shell 42 on Wayland**, primary monitor only. Playback
is muted, mouse interaction is disabled, and a fullscreen window on the primary
monitor pauses rendering. Disabling the extension stops its renderer and reveals
the original background without changing GNOME's wallpaper settings.

## Install

Build or install a Wayland-capable `linux-wallpaperengine` first. GLFW 3.4 was used
for testing; the Ubuntu 22.04 GLFW 3.3.6 package encountered an xdg-shell surface
initialization error. This extension does not install the engine or its libraries.

From the repository root:

```sh
mkdir -p build/gnome
gnome-extensions pack packaging/gnome --force --out-dir=build/gnome
gnome-extensions install --force build/gnome/linux-wallpaperengine@almamu.github.io.shell-extension.zip
```

Log out and log in once so GNOME discovers the new extension. Save your work first.

## Configure and run

Use absolute paths below; GSettings does not expand `~` inside a stored string.
`engine-path` can point to a wrapper that sets up library paths and then `exec`s
the engine. Arguments are passed directly to the executable, not through a shell.

```sh
schema_dir="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/linux-wallpaperengine@almamu.github.io/schemas"
schema=org.gnome.shell.extensions.linux-wallpaperengine

gsettings --schemadir "$schema_dir" set "$schema" engine-path '/absolute/path/to/linux-wallpaperengine'
gsettings --schemadir "$schema_dir" set "$schema" wallpaper '/absolute/path/to/wallpaper-project'
# Optional if the engine cannot locate the Wallpaper Engine assets automatically:
gsettings --schemadir "$schema_dir" set "$schema" assets-dir '/absolute/path/to/assets'
gnome-extensions enable linux-wallpaperengine@almamu.github.io
```

The wallpaper value can also be a Steam workshop ID. Leave `assets-dir` empty to
use the engine's normal asset discovery. No wallpaper files are bundled here.

```sh
gsettings --schemadir "$schema_dir" set "$schema" fps 30
gsettings --schemadir "$schema_dir" set "$schema" paused true
gsettings --schemadir "$schema_dir" set "$schema" paused false
gnome-extensions disable linux-wallpaperengine@almamu.github.io
```

Settings other than `paused` restart the renderer. `paused false` releases manual
pause; a fullscreen window can still keep playback paused. GNOME normally disables
user extensions on the lock screen and re-enables them after unlocking.

If playback does not start, check `gnome-extensions info
linux-wallpaperengine@almamu.github.io` and `journalctl --user -b` for engine or
extension errors. An empty wallpaper setting intentionally starts no process.

## How it works

`Meta.WaylandClient` identifies the extension's own window. A non-reactive
`Clutter.Clone` presents that window in `Main.layoutManager._backgroundGroup`, while
the original stays minimized and hidden from the window list. The extension owns
the renderer process and sends SIGSTOP/SIGCONT for pause, and SIGTERM on disable.
It resumes a stopped child before terminating it. Monitor changes restart playback
on the current primary monitor.

The background group is a private GNOME API. Newer Shell versions need a separate
port and testing; changing the metadata version list alone is not sufficient.

## Verification

Run the isolated integration test with an installed engine and a short looping
video project (requires `dbus-run-session`, GNOME Shell 42, Python GI/GTK3 and Pillow):

```sh
python3 packaging/gnome/smoke-test.py \
  --engine /absolute/path/to/linux-wallpaperengine \
  --wallpaper /absolute/path/to/video-project \
  --assets-dir /absolute/path/to/assets \
  --output build/gnome-test
```

The test starts a separate headless GNOME session. It checks changing background
frames, pause/resume, fullscreen pausing, settings-driven restart, and cleanup on
disable. This is a functional test, not a desktop performance benchmark. Regular
desktop performance, lock/unlock, and mixed-scale multi-monitor behavior still
need testing.
