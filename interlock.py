"""Machine-wide interlocks for the video factory (owner, 2026-11-23; modelled on Video Studio's gpu_lock, our own code).

One job must never step on another. Each named lock is a byte-range lock on a file (msvcrt on Windows, fcntl elsewhere):
  - the OS frees it when the holding process exits or crashes, so a dead job never leaves a stale lock;
  - it works across processes AND threads (every acquire opens its own handle), so two server threads, a CLI run and
    a subprocess all see the same truth;
  - a small JSON beside it says who holds it (pid, what, since) - display only; the OS lock is the truth.

Locks in use:
  workspace - the factory's shared workspace (flow_prompts.txt, scene_NN, narration, the build): one job at a time.
  flow      - the Flow browser (our CDP Chrome on :9222): one driver at a time (topic batches, manual import, the beat
              editor's Flow redo). Taken inside flow_automation.attach_or_launch; a subprocess frees it on exit.
  gpu       - THE SAME machine-wide GPU lock Video Studio uses (its gpu.lock file, byte 0, same metadata keys), so the
              factory and Video Studio take turns on the GPU instead of colliding. Held for one GPU stage at a time (a
              Blender render, a Hunyuan3D mesh, one scene's vision checks) and released - with the VRAM freed - the
              moment that stage ends. Use `with interlock.gpu("what"):`. Video Studio's code is never imported or changed;
              only its lock file is shared, exactly as its own processes share it.

CLI:  python interlock.py status
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl

# a FIXED place, the same for every process however it was started (a WMI-launched server can see a different
# LOCALAPPDATA, which split the locks once): next to this file
LOCK_DIR = Path(os.environ.get("FACTORY_LOCK_DIR") or Path(__file__).resolve().parent / ".locks")
NAMES = ("workspace", "flow", "gpu")


def _gpu_lock_file():
    """Video Studio's GPU lock file: its GPU_LOCK_FILE env / settings 'gpu_lock_file' (read only), else F:/code/gpu.lock."""
    p = os.environ.get("GPU_LOCK_FILE")
    if not p:
        try:
            p = json.loads(Path(r"F:\code\video-studio-test\settings.json").read_text(encoding="utf-8")).get("gpu_lock_file")
        except (OSError, ValueError):
            p = None
    return Path(p or r"F:\code\gpu.lock")


def _paths(name):
    if name == "gpu":
        p = _gpu_lock_file()
        return p, p.with_name(p.name + ".json")
    return LOCK_DIR / f"{name}.lock", LOCK_DIR / f"{name}.lock.json"


def _try(fh):
    fh.seek(0)
    try:
        if os.name == "nt":
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.lockf(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0)
        return True
    except OSError:
        return False


def _open(name):
    p, _ = _paths(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    fh = open(p, "a+b")
    fh.seek(0, os.SEEK_END)
    if fh.tell() == 0:
        fh.write(b"\0")
        fh.flush()
    return fh


def holder(name):
    """Who holds `name` now -> {"pid", "desc", "since", ...} or None when free (checked against the OS lock)."""
    fh = _open(name)
    try:
        if _try(fh):
            _unlock(fh)
            return None
    finally:
        fh.close()
    for _ in range(20):   # the holder may be replacing the file this instant
        try:
            meta = json.loads(_paths(name)[1].read_text(encoding="utf-8"))
            if meta:
                return meta
        except (OSError, ValueError) as e:
            err = repr(e)
        time.sleep(0.05)
    return {"desc": "unknown", "error": locals().get("err", "no details written")}


def _unlock(fh):
    fh.seek(0)
    try:
        if os.name == "nt":
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.lockf(fh.fileno(), fcntl.LOCK_UN, 1, 0)
    except OSError:
        pass


def _write_meta(name, d):
    mp = _paths(name)[1]
    tmp = mp.with_name(f"{mp.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    for _ in range(40):
        try:
            os.replace(tmp, mp)
            return
        except PermissionError:
            time.sleep(0.05)


class Lock:
    """lk = Lock("workspace"); lk.acquire("topic plan", wait=False) -> bool; ...; lk.release().
    Not re-entrant: a second acquire of a held lock (any thread, any process) waits or fails."""

    def __init__(self, name):
        assert name in NAMES, name
        self.name, self.fh = name, None

    def acquire(self, desc, wait=True, timeout=None, on_wait=None, poll=1.0, **extra):
        fh, t0, told = _open(self.name), time.time(), False
        while not _try(fh):
            if not wait or (timeout is not None and time.time() - t0 >= timeout):
                fh.close()
                return False
            if not told and on_wait:
                try:
                    on_wait(holder(self.name) or {})
                except Exception:
                    pass
                told = True
            time.sleep(poll)
        self.fh = fh
        now = time.time()   # "t" is Video Studio's key for the same moment, so its status shows our holder too
        _write_meta(self.name, dict(pid=os.getpid(), desc=desc, t=now, since=now,
                                    since_text=time.strftime("%Y-%m-%d %H:%M:%S"), **extra))
        return True

    def release(self):
        fh, self.fh = self.fh, None
        if fh is None:
            return
        try:
            _write_meta(self.name, {})
            _unlock(fh)
        finally:
            fh.close()

    @property
    def held(self):
        return self.fh is not None


import contextlib


@contextlib.contextmanager
def gpu(desc, log=print, after=None):
    """Hold the shared GPU lock (Video Studio's) for one GPU stage. Waits, saying who holds it. `after()` runs before
    the release - e.g. unloading a model so the VRAM is free for whoever is next."""
    lk = Lock("gpu")
    t0 = time.time()
    lk.acquire("video-factory: " + desc, on_wait=lambda m: log(
        "waiting for the GPU: in use by %s; this step starts when it is free" % describe(m)))
    if time.time() - t0 > 2:
        log("got the GPU after %.0f s" % (time.time() - t0))
    try:
        yield
    finally:
        try:
            if after:
                after()
        finally:
            lk.release()


def describe(meta):
    """A holder's meta -> 'topic plan (pid 123, since 22:29:33)'."""
    if not meta:
        return "nothing"
    t = meta.get("since") or meta.get("t")
    when = time.strftime("%H:%M:%S", time.localtime(t)) if t else "?"
    return f"{meta.get('desc', 'unknown')} (pid {meta.get('pid', '?')}, since {when})"


if __name__ == "__main__":
    if sys.argv[1:2] != ["status"]:
        sys.exit("usage: python interlock.py status")
    for n in NAMES:
        print(f"{n:10} {describe(holder(n))}")
