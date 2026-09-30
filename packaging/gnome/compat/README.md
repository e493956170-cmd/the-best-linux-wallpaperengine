# Ubuntu 22.04 desktop click focus

Ubuntu's Desktop Icons NG (DING) uses transparent Wayland windows above the
wallpaper. Clicking empty space focuses those windows even when the wallpaper
renderer is minimized and its clones are non-reactive.

`ding-42-desktop-focus.patch` targets the DING shipped with Ubuntu 22.04 / GNOME
42. It returns keyboard focus to the previous application after a plain click
on empty desktop space, without raising windows or changing workspaces. Icon
selection, rubber-band dragging, modifier clicks and context menus retain their
usual desktop focus. Closing or minimizing the previous application prevents
focus restoration.

Install a user-local patched DING, leaving the system package intact:

```sh
(
set -e
source_dir=/usr/share/gnome-shell/extensions/ding@rastersoft.com
local_dir="$HOME/.local/share/gnome-shell/extensions/ding@rastersoft.com"
# This deliberately refuses to overwrite an existing local extension.
test ! -e "$local_dir"
cp -r "$source_dir" "$local_dir"
patch --dry-run -d "$local_dir" -p1 < packaging/gnome/compat/ding-42-desktop-focus.patch
patch -d "$local_dir" -p1 < packaging/gnome/compat/ding-42-desktop-focus.patch
)
```

Log out and back in to load the local extension instead of the cached system
copy. This compatibility patch is separate from the wallpaper extension and is
not required on stock GNOME without DING.

To verify in the disposable GNOME test session, add `--desktop-clicks
--desktop-icons /path/to/patched/ding --monitors 2` to the smoke-test command.
The test injects clicks on both backgrounds and checks application focus, then
checks desktop focus for icon selection, rubber-band dragging and context menus.
It uses a temporary desktop directory; the real desktop files are not modified.
