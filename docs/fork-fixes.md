# Changes carried by this fork

The fork starts from upstream `b016d7d1fdcf4e5fd2f9c9fa420a8aaa07fee02d`.
GNOME 42 Wayland integration is submitted upstream as
[PR #686](https://github.com/Almamu/linux-wallpaperengine/pull/686).

## Imported fixes

These commits were cherry-picked with their original authors and source commit IDs.

| Upstream PR | Fix | Local before/after check |
| --- | --- | --- |
| [#634](https://github.com/Almamu/linux-wallpaperengine/pull/634) | Do not open an SDL audio device with `--silent` | Intercepted `SDL_OpenAudioDevice`: called before, absent after; video still renders. |
| [#655](https://github.com/Almamu/linux-wallpaperengine/pull/655) | Handle null PulseAudio introspection operations | Injected null server-info and sink-input operations: both abort before, both keep rendering after. |
| [#643](https://github.com/Almamu/linux-wallpaperengine/pull/643) | Free the correct XQueryTree list and release the outer list on failure | Injected successful/failed inner queries into the actual detector: leaks/double-free before, matching releases after. |

The fork also carries the standard-header and fmt changes needed to build with
Ubuntu 22.04's GCC 11. The engine builds locally and the GNOME headless smoke test
passes with the combined changes.

## Reproduce the regression checks

`tools/check-fork-fixes.py` compiles a process-local preload library and runs five
cases against the engine. It requires a running Wayland session, an X display,
PulseAudio-compatible server, a C compiler, pkg-config, and SDL2/PulseAudio/X11
development headers. It opens small temporary renderer windows and disables core
dumps for the intentionally crashing baseline cases.

```sh
python3 tools/check-fork-fixes.py \
  --engine /absolute/path/to/linux-wallpaperengine \
  --wallpaper /absolute/path/to/video-project \
  --assets-dir /absolute/path/to/assets \
  --output build/fixes-after --expect after
```

Run the same command with an unpatched upstream binary, a separate output directory,
and `--expect before` to verify the original failures. Logs and a JSON result are
written to the selected output directory. The X11 checks inject the query results;
they verify ownership handling, not a long-running X11 desktop memory benchmark.

## PRs reviewed but not imported

- #644 and #674 address the same album-art crash with different implementations;
  an OpenGL fallback regression test is needed before choosing one.
- #618 fixes a vector validation predicate, but the neighboring z/w conversion
  also needs coverage; #619 needs a QuickJS lifetime test.
- #668 and #677 concern layer-shell shutdown/frame pacing. The current GNOME
  environment cannot exercise those compositor paths, so they remain deferred.

The review started from the 32 open upstream PRs on 2026-09-30. Large feature work
and draft PRs were left out of this initial bug-fix batch.
