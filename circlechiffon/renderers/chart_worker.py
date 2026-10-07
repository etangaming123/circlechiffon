"""
Render worker process for `renderers/chart_local.py`.

Run as `python -m circlechiffon.renderers.chart_worker`. Reads one JSON job
per line on stdin, renders that frame range into its own x264, and answers
with one JSON line on stdout: `{"ok": true, "frames": n}` or
`{"ok": false, "error": "..."}`. Stays alive for the next job, so the numpy
and skia imports are paid once per worker rather than once per render.

This is its own entrypoint, rather than a multiprocessing pool, on purpose:
spawn-mode pools (the only kind on Windows) re-import the parent's
`__main__` in every worker - here that's `main.py`, which loads config
(possibly prompting on stdin), imports all of discord.py and builds the bot.

stdout is the protocol channel; nothing else may print to it.

Ctrl+C in the bot's terminal reaches every process in the group, workers
included, and each would print its own KeyboardInterrupt traceback. Workers
ignore SIGINT instead: the bot ends them by closing their stdin (or killing
them), and if it dies outright they exit quietly on the broken pipe.
"""

import json
import signal
import sys


def main() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    from circlechiffon.renderers.chart_local import _ChunkJob, _render_chunk

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            job = _ChunkJob(**json.loads(line))
            reply = {"ok": True, "frames": _render_chunk(job)}
        except Exception as e:  # report it and stay up for the next job
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        try:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, OSError):  # the bot is gone
            break


if __name__ == "__main__":
    main()
