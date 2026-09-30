#!/usr/bin/env python3
"""Check imported audio/X11 fixes against a built engine using fault injection."""

import argparse
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import time


SHIM = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pulse/pulseaudio.h>
#include <SDL.h>
#include <X11/Xlib.h>

static int mode_is(const char *name) {
    const char *mode = getenv("LWE_TEST_MODE");
    return mode && strcmp(mode, name) == 0;
}

pa_operation *pa_context_get_server_info(pa_context *context, pa_server_info_cb_t callback, void *data) {
    if (mode_is("null-server")) {
        fprintf(stderr, "LWE_TEST: injected null server operation\n");
        return NULL;
    }
    pa_operation *(*original)(pa_context *, pa_server_info_cb_t, void *) =
        dlsym(RTLD_NEXT, "pa_context_get_server_info");
    return original(context, callback, data);
}

pa_operation *pa_context_get_sink_input_info_list(pa_context *context, pa_sink_input_info_cb_t callback, void *data) {
    if (mode_is("null-sink")) {
        fprintf(stderr, "LWE_TEST: injected null sink operation\n");
        return NULL;
    }
    pa_operation *(*original)(pa_context *, pa_sink_input_info_cb_t, void *) =
        dlsym(RTLD_NEXT, "pa_context_get_sink_input_info_list");
    return original(context, callback, data);
}

SDL_AudioDeviceID SDL_OpenAudioDevice(const char *device, int capture, const SDL_AudioSpec *desired,
                                    SDL_AudioSpec *obtained, int changes) {
    fprintf(stderr, "LWE_TEST: audio device requested\n");
    SDL_AudioDeviceID (*original)(const char *, int, const SDL_AudioSpec *, SDL_AudioSpec *, int) =
        dlsym(RTLD_NEXT, "SDL_OpenAudioDevice");
    return original(device, capture, desired, obtained, changes);
}

static Window *outer, *inner;
static int outer_freed, inner_freed, phase;

Status XQueryTree(Display *display, Window window, Window *root, Window *parent,
                  Window **children, unsigned int *count) {
    if (!mode_is("x11-success") && !mode_is("x11-failure")) {
        Status (*original)(Display *, Window, Window *, Window *, Window **, unsigned int *) =
            dlsym(RTLD_NEXT, "XQueryTree");
        return original(display, window, root, parent, children, count);
    }
    *root = 1;
    *parent = 1;
    *count = 0;
    if (phase == 0) {
        if (outer && !outer_freed)
            fprintf(stderr, "LWE_TEST: outer list leaked\n");
        if (inner && !inner_freed)
            fprintf(stderr, "LWE_TEST: inner list leaked\n");
        // Track XFree calls separately; defer actual frees so a baseline double-free is observable.
        free(outer);
        free(inner);
        outer = malloc(sizeof(Window));
        inner = NULL;
        outer_freed = inner_freed = 0;
        *children = outer;
        phase = 1;
        return 1;
    }
    phase = 0;
    fprintf(stderr, "LWE_TEST: inner query reached\n");
    if (mode_is("x11-failure")) {
        *children = NULL;
        return 0;
    }
    inner = malloc(sizeof(Window));
    *children = inner;
    return 1;
}

int XFree(void *pointer) {
    if (pointer && pointer == outer) {
        if (outer_freed)
            fprintf(stderr, "LWE_TEST: outer list double-free\n");
        outer_freed = 1;
        fprintf(stderr, "LWE_TEST: outer list freed\n");
        return 0;
    }
    if (pointer && pointer == inner) {
        inner_freed = 1;
        fprintf(stderr, "LWE_TEST: inner list freed\n");
        return 0;
    }
    int (*original)(void *) = dlsym(RTLD_NEXT, "XFree");
    return original(pointer);
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine', type=Path, required=True)
    parser.add_argument('--wallpaper', type=Path, required=True)
    parser.add_argument('--assets-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expect', choices=['before', 'after'], required=True)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    source = args.output / 'fault-injection.c'
    library = args.output / 'fault-injection.so'
    source.write_text(SHIM)
    flags = subprocess.check_output(['pkg-config', '--cflags', 'sdl2', 'libpulse', 'x11'], text=True).split()
    subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Wextra', *flags, str(source),
                    '-ldl', '-o', str(library)], check=True)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    results = {}
    for mode in ('silent', 'null-server', 'null-sink', 'x11-success', 'x11-failure'):
        env = os.environ.copy()
        env.update(LD_PRELOAD=str(library), LWE_TEST_MODE=mode, SDL_AUDIODRIVER='dummy')
        command = [str(args.engine.resolve()), '--window', '0x0x128x72', '--fps', '5',
                   '--silent', '--disable-mouse', '--no-fullscreen-pause',
                   '--assets-dir', str(args.assets_dir.resolve()), str(args.wallpaper.resolve())]
        if mode in ('silent', 'x11-success', 'x11-failure'):
            command.insert(1, '--noautomute')
        if mode.startswith('x11-'):
            env['XDG_SESSION_TYPE'] = 'x11'
            command.remove('--no-fullscreen-pause')
        log_path = args.output / (mode + '.log')
        with log_path.open('w') as log:
            process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 20
                ready_at = None
                while time.monotonic() < deadline and process.poll() is None:
                    if 'VO: [libmpv]' in log_path.read_text():
                        if ready_at is None:
                            ready_at = time.monotonic()
                        if time.monotonic() - ready_at >= 3:
                            break
                    time.sleep(0.1)
                alive = process.poll() is None
            finally:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        output = log_path.read_text()
        if mode.startswith('null-'):
            assert 'injected null ' in output, output[-1500:]
            if args.expect == 'before':
                assert process.returncode == -signal.SIGABRT, process.returncode
            else:
                assert alive and 'VO: [libmpv]' in output, output[-1500:]
        elif mode == 'silent':
            assert alive and 'VO: [libmpv]' in output, output[-1500:]
            assert ('audio device requested' in output) == (args.expect == 'before')
        else:
            assert alive and output.count('inner query reached') >= 2, output[-1500:]
            if args.expect == 'before':
                expected = 'inner list leaked' if mode == 'x11-success' else 'outer list leaked'
                assert expected in output, output[-1500:]
            else:
                assert 'list leaked' not in output and 'double-free' not in output, output[-1500:]
                assert 'outer list freed' in output
                if mode == 'x11-success':
                    assert 'inner list freed' in output
        results[mode] = {'expectation': args.expect, 'passed': True, 'exit_code': process.returncode}
        print('PASS:', args.expect, mode, flush=True)
    (args.output / 'result.json').write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
