"""Static checks on the UI module (no window is opened)."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import app  # noqa: E402
import upscaler  # noqa: E402

SOURCE = (ROOT / 'src/app.py').read_text(encoding='utf-8')


def test_mode_labels_cover_modes():
    assert set(app.MODE_LABELS) == set(upscaler.MODES)


def test_app_has_no_stdout_reconfigure():
    assert 'reconfigure' not in SOURCE


def test_fonts_not_below_11():
    sizes = [int(n) for n in re.findall(r'\("(?:Microsoft JhengHei|Consolas)",\s*(\d+)', SOURCE)]
    assert sizes
    assert min(sizes) >= 11


def test_rates_fall_back_to_default():
    assert set(upscaler.DEFAULT_RATES) == set(upscaler.SCALES)
    assert app.initial_rate({}, 4) == upscaler.DEFAULT_RATES[4]
    assert app.initial_rate({'rates': {'3': 1234.0}}, 3) == 1234.0


def test_merge_selection_appends_without_duplicates():
    assert app.merge_selection('file', ['C:/a.png'], ['C:/b.png']) == ['C:/b.png']
    assert app.merge_selection('folder', ['C:/x'], ['C:/y', 'c:\\X']) == ['C:/x', 'C:/y']
    assert app.merge_selection('files', [], ['C:/a.png', 'C:/a.png']) == ['C:/a.png']


def test_selection_text():
    assert app.selection_text('folder', []) == "尚未選擇"
    assert app.selection_text('folder', ['C:/x']) == "C:/x"
    assert app.selection_text('folder', ['C:/x', 'C:/y']) == "2 個資料夾：C:/x、C:/y"
    assert app.selection_text('files', ['C:/a', 'C:/b', 'C:/c', 'C:/d']) == "4 個檔案：C:/a、C:/b、C:/c 等"


def test_window_size_follows_dpi_and_fits_screen():
    assert app.window_size(3840, 2160, 1.5) == (1650, 1230)
    assert app.window_size(1920, 1080, 1.0) == (1100, 820)
    assert app.window_size(1366, 768, 1.25) == (1229, 652)
