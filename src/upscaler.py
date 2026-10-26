"""Local image upscaling core (no UI). Adapted from an earlier project's scripts/upscale_cg.py.

Writes ONLY <source stem>_x<scale>.png beside each source; originals are never modified.
Requires Pillow and tools/realesrgan official ncnn-vulkan executable/models.
"""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import msvcrt
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

from PIL import Image

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
SCALES = (2, 3, 4)
MODES = ('file', 'files', 'folder', 'folder_recursive')
MODEL = 'realesr-animevideov3'
TILE = 128
# Output pixels/second measured 2026-10-26 on the author's GPU (1024-1280 px CGs, incl. hashing/PNG save).
DEFAULT_RATES = {2: 905_400, 3: 1_987_400, 4: 2_475_700}
MAX_ENGINE_FAILURES = 3
ENGINE_TIMEOUT = 3600
WORKSPACE_NAME = re.compile(r'^job-[0-9a-f]{32}$')


def allow_large_images():
    # Local files the user picked; 4x of a 4096x3072 CG exceeds Pillow's bomb limit.
    Image.MAX_IMAGE_PIXELS = None


allow_large_images()


class EngineError(RuntimeError):
    pass


class Stopped(Exception):
    pass


@contextmanager
def workspace(parent):
    parent = Path(parent).resolve()
    path = parent / ('job-' + uuid.uuid4().hex)
    path.mkdir()
    try:
        yield path
    finally:
        # Use inherited Windows ACLs; tempfile's restrictive mode breaks sandbox writes.
        if path.resolve().parent != parent:
            raise ValueError('Workspace escaped its parent')
        # Antivirus/thumbnail locks must not turn a finished image into a failure;
        # leftovers are excluded from scans by WORKSPACE_NAME.
        shutil.rmtree(path, ignore_errors=True)


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def output_path(source, scale):
    source = Path(source)
    return source.with_name(f'{source.stem}_x{scale}.png')


def has_alpha(im):
    return im.mode in ('RGBA', 'LA', 'PA') or (im.mode == 'P' and 'transparency' in im.info)


def process_one(source, scale, records, settings, engine):
    source = Path(source)
    output = output_path(source, scale)
    if source.resolve() == output.resolve():
        raise ValueError('Output must not replace source')
    key = str(output.resolve())
    signature = {'source_sha256': sha(source), 'settings': settings}
    old = records.get(key)
    if old and output.exists():
        try:
            if all(old.get(k) == v for k, v in signature.items()) and old['output_sha256'] == sha(output):
                with Image.open(output) as im:
                    im.verify()
                return dict(old, status='skipped')
        except (ValueError, KeyError, OSError):
            pass
    start = time.monotonic()
    with workspace(source.parent) as tmp:
        with Image.open(source) as im:
            expected = (im.width * scale, im.height * scale)
            alpha = im.convert('RGBA').getchannel('A') if has_alpha(im) else None
            # ASCII basenames also work with older image-loading libraries.
            im.convert('RGB').save(tmp / 'input.png')
        engine(tmp / 'input.png', tmp / 'output.png')
        with Image.open(tmp / 'output.png') as im:
            if im.size != expected:
                raise ValueError(f'Unexpected output dimensions: {im.size}; wanted {expected}')
            result = im.convert('RGB')
        if alpha is not None:
            # Alpha has no texture detail, so plain resampling is enough.
            result.putalpha(alpha.resize(expected, Image.LANCZOS))
        candidate = tmp / 'result.png'
        result.save(candidate)
        with Image.open(candidate) as im:
            im.verify()
        record = dict(signature, source=str(source), output=str(output),
                      output_sha256=sha(candidate), width=expected[0], height=expected[1],
                      alpha=alpha is not None, seconds=round(time.monotonic()-start, 2), status='done')
        # Interrupted writes cannot create a falsely completed record.
        candidate.replace(output)
    records[key] = record
    return record


# ---- 掃描 ----

def is_upscaled_name(path):
    return re.search(r'_x[234]$', Path(path).stem, re.I) is not None


def collect_images(mode, paths):
    if mode in ('file', 'files'):
        candidates = [Path(p) for p in paths]
    # Folders deleted or renamed after being picked are ignored, not fatal.
    elif mode == 'folder':
        candidates = [p for folder in paths if Path(folder).is_dir() for p in Path(folder).iterdir()]
    elif mode == 'folder_recursive':
        candidates = [p for folder in paths if Path(folder).is_dir() for p in Path(folder).rglob('*')]
    else:
        raise ValueError(f'Unknown mode: {mode}')
    found = {p.resolve() for p in candidates
             if p.is_file() and p.suffix.lower() in IMAGE_EXTS and not is_upscaled_name(p)
             and not any(WORKSPACE_NAME.match(part) for part in p.parent.parts)}
    return sorted(found, key=lambda p: p.as_posix().lower())


def out_pixels(source, scale):
    with Image.open(source) as im:
        return im.width * scale * im.height * scale


# ---- 引擎 ----

def tool_files(tool_dir, scale):
    tool_dir = Path(tool_dir)
    return [tool_dir / 'realesrgan-ncnn-vulkan.exe',
            tool_dir / 'models' / f'{MODEL}-x{scale}.param',
            tool_dir / 'models' / f'{MODEL}-x{scale}.bin']


def missing_tool_files(tool_dir, scale):
    return [str(p) for p in tool_files(tool_dir, scale) if not p.is_file()]


def engine_settings(tool_dir, scale):
    executable, *models = tool_files(tool_dir, scale)
    return {'scale': scale, 'model': MODEL, 'tile': TILE,
            'engine_sha256': sha(executable), 'model_sha256': [sha(p) for p in models]}


def make_engine(tool_dir, scale, log_path, stop_event):
    tool_dir = Path(tool_dir)
    executable = tool_dir / 'realesrgan-ncnn-vulkan.exe'

    def engine(source, destination):
        source, destination = Path(source), Path(destination)
        command = [str(executable), '-i', source.name, '-o', destination.name,
                   '-m', str(tool_dir / 'models'), '-n', MODEL, '-s', str(scale),
                   '-t', str(TILE), '-j', '1:1:1', '-f', 'png']
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with Path(log_path).open('ab') as log:
            child = subprocess.Popen(command, cwd=source.parent, stdout=log, stderr=log,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic() + ENGINE_TIMEOUT
            try:
                while child.poll() is None:
                    if stop_event.is_set():
                        raise Stopped()
                    if time.monotonic() > deadline:
                        raise EngineError(f'Engine timed out after {ENGINE_TIMEOUT} s')
                    time.sleep(0.5)
            except BaseException:
                child.terminate()
                child.wait(timeout=15)
                raise
        if child.returncode != 0:
            raise EngineError(f'Engine exit code {child.returncode}; see {log_path}')
        if source.parent != destination.parent:
            (source.parent / destination.name).replace(destination)

    return engine


@contextmanager
def BatchLock(path):
    # OS lock releases on exit/crash; a leftover lock file is harmless.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as lock:
        lock.seek(0)
        lock.write(b'0')
        lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('Another upscale batch is already running') from exc
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


# ---- 紀錄 ----

def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + '.tmp')
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    pending.replace(path)


# ---- 時間估算 ----

class RateTracker:
    def __init__(self, initial_rate):
        self.initial_rate = initial_rate
        self.pixels = 0
        self.seconds = 0.0

    def add(self, out_pixels, seconds):
        self.pixels += out_pixels
        self.seconds += seconds

    @property
    def measured(self):
        return self.seconds > 0

    @property
    def rate(self):
        return self.pixels / self.seconds if self.measured else self.initial_rate

    def remaining(self, out_pixels):
        return out_pixels / self.rate


def format_duration(seconds):
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f'約 {seconds} 秒'
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f'約 {minutes} 分鐘'
    return f'約 {minutes // 60} 小時 {minutes % 60} 分鐘'


# ---- 批次 ----

def run_batch(sources, scale, *, records_path, log_dir, settings, engine, on_event,
              stop_event, initial_rate):
    records = load_json(records_path, {})
    sizes = []
    for source in sources:
        try:
            sizes.append(out_pixels(source, scale))
        except OSError:
            sizes.append(0)
    tracker = RateTracker(initial_rate)
    counts = {'done': 0, 'skipped': 0, 'failed': 0}
    failures = []
    stopped = aborted = False
    engine_failures = 0
    claimed = {}
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / ('run-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.jsonl')
    with log_path.open('a', encoding='utf-8') as log:
        for index, source in enumerate(sources):
            if stop_event.is_set():
                stopped = True
                break
            on_event('start', {'index': index, 'total': len(sources), 'source': source})
            try:
                # cg01.jpg and cg01.png would both write cg01_x2.png; first one wins.
                owner = claimed.setdefault(output_path(source, scale).resolve(), source)
                if owner != source:
                    raise ValueError(f'Output name collides with {owner.name}; rename one of them')
                record = process_one(source, scale, records, settings, engine)
                engine_failures = 0
            except Stopped:
                stopped = True
                break
            except Exception as exc:
                engine_failures = engine_failures + 1 if isinstance(exc, EngineError) else 0
                record = {'source': str(source), 'status': 'failed', 'error': str(exc)}
                failures.append((source, str(exc)))
            counts[record['status']] += 1
            if record['status'] == 'done':
                tracker.add(sizes[index], record['seconds'])
                save_json(records_path, records)
            log.write(json.dumps(record, ensure_ascii=False) + '\n')
            log.flush()
            on_event('result', {'index': index, 'record': record})
            on_event('progress', {'done': index + 1, 'total': len(sources),
                                  'remaining_seconds': tracker.remaining(sum(sizes[index + 1:])),
                                  'rate': tracker.rate})
            if engine_failures >= MAX_ENGINE_FAILURES:
                # Likely a GPU/driver problem; stop rather than fail the whole night.
                aborted = True
                break
    result = {'counts': counts, 'stopped': stopped, 'aborted': aborted, 'failures': failures,
              'rate': tracker.rate if tracker.measured else None, 'log': str(log_path)}
    on_event('finished', result)
    return result
