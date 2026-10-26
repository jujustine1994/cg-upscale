"""pytest tests -v"""
from pathlib import Path
import shutil
import sys
import uuid

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import upscaler as tool  # noqa: E402

SETTINGS = {'scale': 2, 'model': 'fixture'}


class FakeEngine:
    """Stands in for realesrgan: plain resize, counts calls."""

    def __init__(self, scale=2):
        self.scale = scale
        self.calls = 0

    def __call__(self, source, output):
        self.calls += 1
        with Image.open(source) as im:
            assert im.mode == 'RGB'
            im.resize((im.width * self.scale, im.height * self.scale)).save(output)


@pytest.fixture
def root():
    base = Path(__file__).resolve().parents[1] / '.tmp/tests'
    base.mkdir(parents=True, exist_ok=True)
    path = base / ('fixture-' + uuid.uuid4().hex)
    path.mkdir()
    yield path
    shutil.rmtree(path)


@pytest.fixture
def src(root):
    path = root / 'input.png'
    Image.new('RGB', (24, 16), 'red').save(path)
    return path


def run(source, records, engine, scale=2, settings=None):
    return tool.process_one(source, scale, records, settings or SETTINGS, engine)


def test_resume_and_source_unchanged(src):
    before = src.read_bytes()
    records, engine = {}, FakeEngine()
    assert run(src, records, engine)['status'] == 'done'
    assert run(src, records, engine)['status'] == 'skipped'
    assert engine.calls == 1
    assert src.read_bytes() == before
    out = src.with_name('input_x2.png')
    assert tool.output_path(src, 2) == out
    with Image.open(out) as im:
        assert im.size == (48, 32)


def test_changed_source_or_corrupt_output_is_reprocessed(src):
    records, engine = {}, FakeEngine()
    run(src, records, engine)
    Image.new('RGB', (24, 16), 'blue').save(src)
    assert run(src, records, engine)['status'] == 'done'
    tool.output_path(src, 2).write_bytes(b'broken')
    assert run(src, records, engine)['status'] == 'done'
    assert engine.calls == 3


def test_wrong_size_and_engine_failure_never_mark_success(src):
    records = {}

    def wrong(source, output):
        Image.new('RGB', (1, 1)).save(output)

    with pytest.raises(ValueError):
        run(src, records, wrong)
    assert records == {}
    assert not tool.output_path(src, 2).exists()

    def failure(source, output):
        raise tool.EngineError('engine failed')

    with pytest.raises(tool.EngineError):
        run(src, records, failure)
    assert records == {}
    assert not tool.output_path(src, 2).exists()


def test_rgba_keeps_alpha(root):
    path = root / 'alpha.png'
    im = Image.new('RGBA', (24, 16), (255, 0, 0, 255))
    for x in range(12):
        for y in range(16):
            im.putpixel((x, y), (255, 0, 0, 0))
    im.save(path)
    record = run(path, {}, FakeEngine())
    assert record['alpha'] is True
    with Image.open(tool.output_path(path, 2)) as out:
        assert out.mode == 'RGBA'
        assert out.size == (48, 32)
        assert out.getpixel((2, 16))[3] < 10
        assert out.getpixel((46, 16))[3] > 245


def test_palette_transparency_kept(root):
    path = root / 'palette.png'
    Image.new('P', (24, 16), 0).save(path, transparency=0)
    run(path, {}, FakeEngine())
    with Image.open(tool.output_path(path, 2)) as out:
        assert out.mode == 'RGBA'


def test_cmyk_jpg_becomes_rgb(root):
    path = root / 'cmyk.jpg'
    Image.new('CMYK', (24, 16), (0, 255, 255, 0)).save(path)
    record = run(path, {}, FakeEngine())
    assert record['alpha'] is False
    with Image.open(tool.output_path(path, 2)) as out:
        assert out.mode == 'RGB'


def test_unicode_and_space_path(root):
    folder = root / '測試 圖'
    folder.mkdir()
    path = folder / '角色 01.png'
    Image.new('RGB', (24, 16), 'green').save(path)
    assert run(path, {}, FakeEngine())['status'] == 'done'
    assert (folder / '角色 01_x2.png').exists()


def test_existing_output_without_record_is_reprocessed(src):
    Image.new('RGB', (1, 1)).save(tool.output_path(src, 2))
    assert run(src, {}, FakeEngine())['status'] == 'done'
    with Image.open(tool.output_path(src, 2)) as out:
        assert out.size == (48, 32)


def test_workspace_cleaned(src):
    run(src, {}, FakeEngine())

    def failure(source, output):
        raise tool.EngineError('engine failed')

    with pytest.raises(tool.EngineError):
        run(src, {}, failure, settings={'scale': 2, 'model': 'other'})
    assert not list(src.parent.glob('job-*'))


# ---- Task 3: scanning, batch, ETA ----
import threading  # noqa: E402


def make(path, size=(24, 16)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', size, 'red').save(path)
    return path


def batch(sources, root, engine, scale=2, stop_event=None, events=None):
    events = [] if events is None else events
    return tool.run_batch(
        sources, scale, records_path=root / 'cache/records.json', log_dir=root / 'logs',
        settings={'scale': scale, 'model': 'fixture'}, engine=engine,
        on_event=lambda kind, data: events.append((kind, data)),
        stop_event=stop_event or threading.Event(), initial_rate=100.0)


def test_collect_modes(root):
    folder = root / 'pics'
    a, b = make(folder / 'a.png'), make(folder / 'b.JPG')
    c = make(folder / 'c_x2.png')
    make(folder / 'd_X4.png')
    (folder / 'note.txt').write_text('x')
    e = make(folder / 'sub/e.webp')
    resolved = lambda paths: [p.resolve() for p in paths]  # noqa: E731
    assert tool.collect_images('folder', [str(folder)]) == resolved([a, b])
    assert tool.collect_images('folder_recursive', [str(folder)]) == resolved([a, b, e])
    assert tool.collect_images('files', [str(a), str(c)]) == resolved([a])
    assert tool.collect_images('file', [str(a)]) == resolved([a])


def test_collect_multiple_and_overlapping_folders(root):
    a, b = make(root / 'one/a.png'), make(root / 'two/b.png')
    c = make(root / 'one/sub/c.png')
    folders = [str(root / 'one'), str(root / 'two'), str(root / 'one/sub')]
    assert tool.collect_images('folder', folders) == [p.resolve() for p in (a, c, b)]
    assert tool.collect_images('folder_recursive', folders) == [p.resolve() for p in (a, c, b)]


def test_collect_ignores_paths_removed_after_selection(root):
    a = make(root / 'one/a.png')
    gone = [str(root / 'gone'), str(root / 'gone.png')]
    assert tool.collect_images('folder', [str(root / 'one'), gone[0]]) == [a.resolve()]
    assert tool.collect_images('folder_recursive', [gone[0]]) == []
    assert tool.collect_images('files', [str(a), gone[1]]) == [a.resolve()]


def test_collect_unknown_mode_raises(root):
    with pytest.raises(ValueError):
        tool.collect_images('everything', [str(root)])


def test_rate_tracker():
    tracker = tool.RateTracker(100)
    assert tracker.remaining(1000) == 10
    tracker.add(400, 2)
    assert tracker.rate == 200


def test_format_duration():
    assert tool.format_duration(45) == '約 45 秒'
    assert tool.format_duration(660) == '約 11 分鐘'
    assert tool.format_duration(4800) == '約 1 小時 20 分鐘'


def test_out_pixels(src):
    assert tool.out_pixels(src, 3) == 24 * 3 * 16 * 3


def test_batch_continues_after_bad_image(root):
    good1, good2 = make(root / 'a.png'), make(root / 'c.png')
    bad = root / 'b.png'
    bad.write_bytes(b'broken')
    events = []
    result = batch([good1, bad, good2], root, FakeEngine(), events=events)
    assert result['counts'] == {'done': 2, 'skipped': 0, 'failed': 1}
    finished = [d for k, d in events if k == 'finished'][0]
    assert len(finished['failures']) == 1
    assert list((root / 'logs').glob('run-*.jsonl'))


def test_batch_stops_after_three_engine_errors(root):
    sources = [make(root / f'{i}.png') for i in range(5)]
    calls = []

    def failure(source, output):
        calls.append(source)
        raise tool.EngineError('gpu lost')

    result = batch(sources, root, failure)
    assert len(calls) == 3
    assert result['counts']['failed'] == 3


def test_stop_leaves_no_output_or_record(root):
    path = make(root / 'a.png')
    stop = threading.Event()

    def stopping(source, output):
        stop.set()
        raise tool.Stopped()

    result = batch([path], root, stopping, stop_event=stop)
    assert result['stopped'] is True
    assert result['counts']['failed'] == 0
    assert not tool.output_path(path, 2).exists()
    assert tool.load_json(root / 'cache/records.json', {}) == {}
    assert batch([path], root, FakeEngine())['counts']['done'] == 1


def test_different_scales_coexist(root):
    path = make(root / 'a.png')
    batch([path], root, FakeEngine(2), scale=2)
    events = []
    batch([path], root, FakeEngine(4), scale=4, events=events)
    assert tool.output_path(path, 2).exists()
    assert tool.output_path(path, 4).exists()
    record = [d['record'] for k, d in events if k == 'result'][0]
    assert record['status'] == 'done'


def test_lock_blocks_second_batch(root):
    lock = root / 'cache/batch.lock'
    with tool.BatchLock(lock):
        with pytest.raises(RuntimeError):
            with tool.BatchLock(lock):
                pass


def test_records_survive_restart(root):
    sources = [make(root / 'a.png'), make(root / 'b.png')]
    batch(sources, root, FakeEngine())
    assert len(tool.load_json(root / 'cache/records.json', {})) == 2
    result = batch(sources, root, FakeEngine())
    assert result['counts'] == {'done': 0, 'skipped': 2, 'failed': 0}


def test_progress_reports_remaining(root):
    sources = [make(root / 'a.png'), make(root / 'b.png')]
    events = []
    batch(sources, root, FakeEngine(), events=events)
    progress = [d for k, d in events if k == 'progress']
    assert [p['done'] for p in progress] == [1, 2]
    assert progress[-1]['remaining_seconds'] == 0


def test_load_json_bad_file_returns_default(root):
    path = root / 'bad.json'
    path.write_text('{oops', encoding='utf-8')
    assert tool.load_json(path, {'a': 1}) == {'a': 1}
    tool.save_json(path, {'b': 2})
    assert tool.load_json(path, {}) == {'b': 2}


# ---- Final review fixes ----

def test_collect_skips_leftover_workspaces(root):
    folder = root / 'pics'
    a = make(folder / 'a.png')
    make(folder / ('job-' + 'ab' * 16) / 'input.png')
    assert tool.collect_images('folder_recursive', [str(folder)]) == [a.resolve()]


def test_same_stem_outputs_do_not_collide(root):
    jpg = root / 'cg01.jpg'
    Image.new('RGB', (24, 16), 'red').save(jpg)
    png = make(root / 'cg01.png')
    events = []
    result = batch([jpg, png], root, FakeEngine(), events=events)
    assert result['counts'] == {'done': 1, 'skipped': 0, 'failed': 1}
    assert 'cg01' in result['failures'][0][1]
    assert batch([jpg, png], root, FakeEngine())['counts']['skipped'] == 1


def test_large_output_is_not_rejected_as_bomb(src):
    limit = Image.MAX_IMAGE_PIXELS
    try:
        Image.MAX_IMAGE_PIXELS = 1000  # simulate output beyond Pillow's limit
        tool.allow_large_images()
        assert run(src, {}, FakeEngine())['status'] == 'done'
    finally:
        Image.MAX_IMAGE_PIXELS = limit


def test_engine_abort_is_reported(root):
    sources = [make(root / f'{i}.png') for i in range(4)]

    def failure(source, output):
        raise tool.EngineError('gpu lost')

    assert batch(sources, root, failure)['aborted'] is True
    assert batch(sources[:1], root, FakeEngine())['aborted'] is False


def test_locked_workspace_cleanup_does_not_fail_done_image(src, monkeypatch):
    real_rmtree = shutil.rmtree

    def locked(path, *args, **kwargs):
        if kwargs.get('ignore_errors'):
            return real_rmtree(path, *args, **kwargs)
        raise PermissionError('locked by antivirus')

    monkeypatch.setattr(tool.shutil, 'rmtree', locked)
    records = {}
    assert run(src, records, FakeEngine())['status'] == 'done'
    assert records
