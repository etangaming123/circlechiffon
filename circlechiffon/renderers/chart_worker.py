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
"""

import json
import sys


def main() -> None:
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
        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
