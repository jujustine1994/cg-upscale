"""
CG Upscale — 本機 Real-ESRGAN 圖片放大工具
選單張／多張／資料夾／含子資料夾，放大版另存在原圖旁（原檔名_x倍率.png），原圖不動。
"""

import ctypes
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from PIL import Image

import upscaler

ROOT = Path(__file__).resolve().parents[1]
TOOL_DIR = ROOT / 'tools/realesrgan'
CACHE_DIR = ROOT / 'cache'
LOG_DIR = ROOT / 'logs'
SETTINGS_PATH = CACHE_DIR / 'settings.json'
RECORDS_PATH = CACHE_DIR / 'records.json'
LOCK_PATH = CACHE_DIR / 'batch.lock'

F = ("Microsoft JhengHei", 12)       # 主要文字
FS = ("Microsoft JhengHei", 11)      # 次要／說明文字／status bar
FB = ("Microsoft JhengHei", 12, "bold")
FM = ("Consolas", 12)                # monospace / log 區
FL = ("Microsoft JhengHei", 14, "bold")  # 張數與預估時間

# 高 DPI 感知：解決 4K/2K 螢幕文字模糊（需在 Tk() 前呼叫）
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

# 存進設定的值 → 畫面顯示文字
MODE_LABELS = {
    'file': '單張圖片',
    'files': '多張圖片',
    'folder': '資料夾',
    'folder_recursive': '資料夾（含子資料夾）',
}
STATUS_LABELS = {
    'pending': '待處理',
    'running': '處理中',
    'done': '完成',
    'skipped': '跳過',
    'failed': '失敗',
}
FILETYPES = [('圖片', ' '.join('*' + ext for ext in sorted(upscaler.IMAGE_EXTS))), ('所有檔案', '*.*')]

THEMES = {
    "light": {
        "name": "清爽白",
        "sv": "light",
        "log_bg": "#F4F4F4",
        "log_fg": "#333333",
        "frame_title": "#1A6BAF",
        "label_fg": "",
        "btn_bg": "#0078D4",
        "btn_fg": "#FFFFFF",
        "win_bg": "#F3F3F3",
        "card_bg": "#FFFFFF",
        "border": "#D0D0D0",
        "pbar": "#0078D4",
    },
    "dark": {
        "name": "深色模式",
        "sv": "dark",
        "log_bg": "#1A1A1A",
        "log_fg": "#C8C8C8",
        "frame_title": "#60AAFF",
        "label_fg": "",
        "btn_bg": "#3A7BD5",
        "btn_fg": "#FFFFFF",
        "win_bg": "#202020",
        "card_bg": "#2A2A2A",
        "border": "#444444",
        "pbar": "#4A90E2",
    },
    "financial": {
        "name": "金融藍",
        "sv": "light",
        "log_bg": "#F0F5FF",
        "log_fg": "#1B2B45",
        "frame_title": "#1B3A6B",
        "label_fg": "#1B3A6B",
        "btn_bg": "#1B3A6B",
        "btn_fg": "#F5C518",
        "win_bg": "#EEF2F8",
        "card_bg": "#FFFFFF",
        "border": "#BDD0EA",
        "pbar": "#1B3A6B",
    },
}


def show_cth_banner():
    b = "\033[90m"   # 邊框：深灰
    c = "\033[96m"   # CTH 字母：亮青
    y = "\033[93m"   # 署名：金黃
    r = "\033[0m"    # reset

    print(f"{b}/*  ================================  *\\{r}")
    print(f"{b} *                                    *{r}")
    print(f"{b} *    {c}██████╗████████╗██╗  ██╗{b}        *{r}")
    print(f"{b} *   {c}██╔════╝   ██║   ██║  ██║{b}        *{r}")
    print(f"{b} *   {c}██║        ██║   ███████║{b}        *{r}")
    print(f"{b} *   {c}██║        ██║   ██╔══██║{b}        *{r}")
    print(f"{b} *   {c}╚██████╗   ██║   ██║  ██║{b}        *{r}")
    print(f"{b} *    {c}╚═════╝   ╚═╝   ╚═╝  ╚═╝{b}        *{r}")
    print(f"{b} *                                    *{r}")
    print(f"{b} *          {y}created by CTH{b}            *{r}")
    print(f"{b}\\*  ================================  */{r}")
    print()


def initial_rate(settings, scale):
    return settings.get('rates', {}).get(str(scale), upscaler.DEFAULT_RATES[scale])


def window_size(screen_w, screen_h, factor, base=(1100, 820)):
    """DPI 感知下 geometry 用實際像素：基本尺寸乘縮放比例，寬最多佔螢幕 90%、高 85%（留工作列）。"""
    return (min(round(base[0] * factor), int(screen_w * 0.9)),
            min(round(base[1] * factor), int(screen_h * 0.85)))


def merge_selection(mode, current, picked):
    """單張模式取代；其他模式累加，同一路徑（不分大小寫、斜線方向）只留一次。"""
    merged = [] if mode == 'file' else list(current)
    seen = {os.path.normcase(os.path.normpath(p)) for p in merged}
    for path in picked:
        key = os.path.normcase(os.path.normpath(path))
        if key not in seen:
            seen.add(key)
            merged.append(path)
    return merged


def selection_text(mode, paths, limit=3):
    if not paths:
        return "尚未選擇"
    if len(paths) == 1:
        return paths[0]
    unit = "個資料夾" if mode.startswith('folder') else "個檔案"
    shown = "、".join(paths[:limit]) + (" 等" if len(paths) > limit else "")
    return f"{len(paths)} {unit}：{shown}"


def read_sizes(sources):
    sizes = []
    for source in sources:
        try:
            with Image.open(source) as im:
                sizes.append(im.size)
        except Exception:
            sizes.append(None)
    return sizes


class ToolApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CG Upscale 圖片放大")
        self.ui_scale = self.root.winfo_fpixels('1i') / 96   # Windows 縮放比例，150% → 1.5
        w, h = window_size(self.root.winfo_screenwidth(), self.root.winfo_screenheight(), self.ui_scale)
        self.root.geometry(f"{w}x{h}")     # 明確設定一次 → 清單增加不撐大視窗
        self.root.resizable(True, True)
        self.root.minsize(round(760 * self.ui_scale), round(560 * self.ui_scale))

        self.msg_queue: queue.Queue = queue.Queue()
        self.is_running = False
        self._closing = False
        self.stop_event = threading.Event()
        self.settings = upscaler.load_json(SETTINGS_PATH, {})
        self._current_theme = self.settings.get("theme", "light")

        self.selected: list[str] = []
        self.sources: list[Path] = []
        self.sizes: list = []
        self.failures: list = []
        self._done_pixels = 0

        self._build_ui()
        self._apply_theme(self._current_theme)
        self._poll_queue()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        pad = {"padx": 14, "pady": 6}
        self.root.columnconfigure(0, weight=1)

        # === 來源 ===
        frame_source = ttk.LabelFrame(self.root, text=" 來源 ", padding=8)
        frame_source.grid(row=0, column=0, sticky="ew", **pad)
        frame_source.columnconfigure(1, weight=1)
        mode = self.settings.get('mode', 'folder')
        self.mode_var = tk.StringVar(value=mode if mode in MODE_LABELS else 'folder')
        frame_modes = ttk.Frame(frame_source)
        frame_modes.grid(row=0, column=0, columnspan=2, sticky="w")
        self.mode_buttons = []
        for key, label in MODE_LABELS.items():
            btn = ttk.Radiobutton(frame_modes, text=label, variable=self.mode_var, value=key,
                                  command=self._on_mode_change)
            btn.pack(side="left", padx=(0, 16))
            self.mode_buttons.append(btn)
        frame_pick = ttk.Frame(frame_source)
        frame_pick.grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.btn_choose = ttk.Button(frame_pick, text="選擇…", command=self._choose, width=10)
        self.btn_choose.pack(side="left")
        self.btn_rescan = ttk.Button(frame_pick, text="重新掃描", command=self._rescan, width=10)
        self.btn_rescan.pack(side="left", padx=(8, 0))
        self.btn_clear = ttk.Button(frame_pick, text="清空", command=self._clear, width=8)
        self.btn_clear.pack(side="left", padx=(8, 0))
        self.path_label = ttk.Label(frame_source, text="尚未選擇", font=FS, foreground="gray")
        self.path_label.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(8, 0))
        self._update_choose_text()

        # === 倍率 ===
        frame_scale = ttk.LabelFrame(self.root, text=" 放大倍率 ", padding=8)
        frame_scale.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 6))
        scale = self.settings.get('scale', 2)
        self.scale_var = tk.IntVar(value=scale if scale in upscaler.SCALES else 2)
        self.scale_buttons = []
        for value in upscaler.SCALES:
            btn = ttk.Radiobutton(frame_scale, text=f"{value} 倍", variable=self.scale_var,
                                  value=value, command=self._on_scale_change)
            btn.pack(side="left", padx=(0, 16))
            self.scale_buttons.append(btn)
        ttk.Label(frame_scale, text="放大版存在原圖旁：原檔名_x倍率.png",
                  font=FS, foreground="gray").pack(side="left", padx=(16, 0))

        # === 檔案清單 ===
        frame_list = ttk.LabelFrame(self.root, text=" 檔案清單 ", padding=8)
        frame_list.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 6))
        self.root.rowconfigure(2, weight=1)
        frame_list.columnconfigure(0, weight=1)
        frame_list.rowconfigure(0, weight=1)
        columns = ("name", "size", "out", "status")
        self.tree = ttk.Treeview(frame_list, columns=columns, show="headings", height=10)
        for col, text, width, anchor in (("name", "檔名", 480, "w"), ("size", "原尺寸", 130, "center"),
                                         ("out", "輸出尺寸", 150, "center"), ("status", "狀態", 100, "center")):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=round(width * self.ui_scale), anchor=anchor, stretch=(col == "name"))
        scrollbar = ttk.Scrollbar(frame_list, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # === 按鈕列 ===
        frame_btn = tk.Frame(self.root)
        frame_btn.grid(row=3, column=0, pady=6)
        self.summary_label = ttk.Label(frame_btn, text="共 0 張", font=FL)
        self.summary_label.pack(side="left", padx=(0, 20))
        self.btn_start = ttk.Button(frame_btn, text="▶  開始", command=self._start, width=14)
        self.btn_start.pack(side="left", ipady=6)
        self.btn_stop = ttk.Button(frame_btn, text="■  停止", command=self._stop, width=10, state="disabled")
        self.btn_stop.pack(side="left", ipady=6, padx=(8, 0))
        ttk.Button(frame_btn, text="開啟所在資料夾", command=self._open_folder).pack(
            side="left", ipady=6, padx=(8, 0))
        self.btn_failures = ttk.Button(frame_btn, text="查看失敗", command=self._show_failures)
        # btn_failures 預設不 pack，有失敗才顯示
        ttk.Button(frame_btn, text="⚙", command=self._open_settings, width=4).pack(
            side="right", ipady=6, padx=(8, 0))

        # === 處理進度 ===
        frame_progress = ttk.LabelFrame(self.root, text=" 處理進度 ", padding=8)
        frame_progress.grid(row=4, column=0, sticky="ew", padx=14, pady=(0, 10))
        self.progress_label = ttk.Label(frame_progress, text="等待開始...")
        self.progress_label.pack(anchor="w")
        self.progress_bar = ttk.Progressbar(frame_progress, mode="determinate")
        self.progress_bar.pack(fill="x", pady=(4, 4))
        self.current_label = ttk.Label(frame_progress, text="", font=FS, foreground="gray")
        self.current_label.pack(anchor="w")

        self._build_status_bar()

    def _build_status_bar(self):
        sep = ttk.Separator(self.root, orient="horizontal")
        sep.grid(row=99, column=0, sticky="ew")
        self._status_bar = tk.Label(self.root, text="就緒", anchor="w", padx=10, pady=4,
                                    font=FS, foreground="gray")
        self._status_bar.grid(row=100, column=0, sticky="ew")

    # ---- 主題 ----

    # 清單是動態 Treeview，依 tkinter-ui INDEX 不啟用 sv-ttk。
    USE_SV_TTK = False

    def _apply_theme(self, theme_key: str):
        t = THEMES.get(theme_key, THEMES["light"])
        if self.USE_SV_TTK:
            try:
                import sv_ttk
                sv_ttk.set_theme(t["sv"])
            except ImportError:
                pass

        from tkinter import font as tkfont
        for fname in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(fname).configure(family="Microsoft JhengHei", size=12)

        style = ttk.Style()
        label_fg = t["label_fg"]
        style.configure("TButton", font=F)
        style.configure("TLabelframe.Label", font=F, foreground=t["frame_title"])
        style.configure("Treeview", font=F, rowheight=round(30 * self.ui_scale))
        style.configure("Treeview.Heading", font=FB)
        for w in ("TLabel", "TCheckbutton", "TRadiobutton"):
            kw = {"font": F}
            if label_fg:
                kw["foreground"] = label_fg
            style.configure(w, **kw)
        self._current_theme = theme_key

    # ---- 設定視窗 ----

    def _open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("設定")
        win.resizable(False, False)
        win.grab_set()

        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="配色主題").pack(anchor="w", pady=(0, 10))
        theme_var = tk.StringVar(value=self._current_theme)
        for key, info in THEMES.items():
            ttk.Radiobutton(frame, text=info["name"], variable=theme_var, value=key).pack(anchor="w", pady=4)

        def _apply():
            self._apply_theme(theme_var.get())
            self._save_settings(theme=theme_var.get())
            win.destroy()

        btn_row = ttk.Frame(win)
        btn_row.pack(pady=(0, 16))
        ttk.Button(btn_row, text="套用", command=_apply, width=10).pack(side="left", padx=4, ipady=4)
        ttk.Button(btn_row, text="取消", command=win.destroy, width=10).pack(side="left", padx=4, ipady=4)

    # ---- 設定檔 ----

    def _save_settings(self, **changes):
        self.settings.update(changes)
        try:
            upscaler.save_json(SETTINGS_PATH, self.settings)
        except OSError:
            pass

    # ---- 選取來源 ----

    def _on_mode_change(self):
        self._save_settings(mode=self.mode_var.get())
        self._update_choose_text()
        self._clear()

    def _update_choose_text(self):
        # 單張是取代；其他模式每按一次加一批進清單
        self.btn_choose.config(text="選擇…" if self.mode_var.get() == 'file' else "加入…")

    def _clear(self):
        self.selected = []
        self._set_sources([], [])
        self.path_label.config(text="尚未選擇", foreground="gray")
        self._set_status("就緒", "info")

    def _rescan(self):
        if not self.selected:
            self._set_status("請先選擇圖片或資料夾", "error")
            return
        self._scan(self.mode_var.get(), self.selected)

    def _scan(self, mode, paths):
        self.summary_label.config(text="掃描中…")
        self._set_status("掃描中…", "info")
        self._set_controls(False)
        threading.Thread(target=self._scan_worker, args=(mode, list(paths)), daemon=True).start()

    def _on_scale_change(self):
        self._save_settings(scale=self.scale_var.get())
        self._refresh_list()

    def _choose(self):
        mode = self.mode_var.get()
        initial = self.settings.get('last_dir') or str(Path.home())
        if mode == 'file':
            picked = filedialog.askopenfilename(title="選擇圖片", initialdir=initial, filetypes=FILETYPES)
            paths = [picked] if picked else []
        elif mode == 'files':
            paths = list(filedialog.askopenfilenames(title="選擇多張圖片", initialdir=initial,
                                                     filetypes=FILETYPES))
        else:
            picked = filedialog.askdirectory(title="選擇資料夾", initialdir=initial)
            paths = [picked] if picked else []
        if not paths:
            return
        self.selected = merge_selection(mode, self.selected, paths)
        first = Path(paths[0])
        self._save_settings(last_dir=str(first if mode.startswith('folder') else first.parent))
        self.path_label.config(text=selection_text(mode, self.selected), foreground="")
        self._scan(mode, self.selected)

    def _scan_worker(self, mode, paths):
        try:
            sources = upscaler.collect_images(mode, paths)
            self.msg_queue.put(("scanned", (sources, read_sizes(sources))))
        except Exception as exc:
            self.msg_queue.put(("scan_error", str(exc)))

    def _set_sources(self, sources, sizes):
        self.sources, self.sizes = sources, sizes
        self.tree.delete(*self.tree.get_children())
        for index, (source, size) in enumerate(zip(sources, sizes)):
            self.tree.insert("", "end", iid=str(index), values=(
                source.name, f"{size[0]}×{size[1]}" if size else "無法讀取", "", STATUS_LABELS['pending']))
        self._refresh_list()

    def _refresh_list(self):
        scale = self.scale_var.get()
        for index, size in enumerate(self.sizes):
            out = f"{size[0] * scale}×{size[1] * scale}" if size else ""
            self.tree.set(str(index), "out", out)
        total = self._total_pixels()
        if self.sources:
            eta = upscaler.format_duration(total / initial_rate(self.settings, scale))
            self.summary_label.config(text=f"共 {len(self.sources)} 張，預估{eta}")
        else:
            self.summary_label.config(text="共 0 張")

    def _total_pixels(self):
        scale = self.scale_var.get()
        return sum(w * h * scale * scale for w, h in (s for s in self.sizes if s))

    def _on_tree_select(self, _event):
        picked = self.tree.selection()
        if picked:
            self._set_status(str(self.sources[int(picked[0])]), "info")

    # ---- UI 互動 ----

    def _set_controls(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for widget in (*self.mode_buttons, *self.scale_buttons, self.btn_choose, self.btn_rescan,
                       self.btn_clear, self.btn_start):
            widget.config(state=state)

    def _on_close(self):
        if self.is_running:
            if not messagebox.askyesno("確認關閉", "放大執行中，確定要關閉嗎？\n目前這張不會留下半成品，下次可續跑。"):
                return
            # 等背景停下再關，避免放大程式在背景繼續跑
            self._closing = True
            self.stop_event.set()
            self._set_status("停止中，請稍候…", "info")
            return
        self.root.destroy()

    def _open_folder(self):
        if self.sources:
            os.startfile(self.sources[0].parent)
        elif self.selected:
            target = Path(self.selected[0])
            os.startfile(target if target.is_dir() else target.parent)

    def _show_failures(self):
        win = tk.Toplevel(self.root)
        win.title("失敗清單")
        win.geometry("760x360")
        text = scrolledtext.ScrolledText(win, font=FM, wrap="word")
        text.pack(fill="both", expand=True, padx=10, pady=10)
        t = THEMES.get(self._current_theme, THEMES["light"])
        text.config(bg=t["log_bg"], fg=t["log_fg"])
        for source, error in self.failures:
            text.insert("end", f"{source}\n  處理失敗，原因：{error}\n\n")
        text.config(state="disabled")

    # ---- 執行 ----

    def _start(self):
        if not self.sources:
            messagebox.showerror("錯誤", "請先選擇要放大的圖片或資料夾")
            return
        scale = self.scale_var.get()
        missing = upscaler.missing_tool_files(TOOL_DIR, scale)
        if missing:
            messagebox.showerror("缺少放大程式", "找不到以下檔案：\n" + "\n".join(missing))
            return
        for iid in self.tree.get_children():
            self.tree.set(iid, "status", STATUS_LABELS['pending'])
        self.failures = []
        self._done_pixels = 0
        self.btn_failures.pack_forget()
        self.progress_bar["maximum"] = max(1, self._total_pixels())
        self.progress_bar["value"] = 0
        self.progress_label.config(text="準備中...")
        self.stop_event.clear()
        self.is_running = True
        self._set_controls(False)
        self.btn_stop.config(state="normal")
        self._set_status("放大中…", "info")
        threading.Thread(target=self._worker, args=(list(self.sources), scale), daemon=True).start()

    def _stop(self):
        self.stop_event.set()
        self.btn_stop.config(state="disabled")
        self._set_status("停止中，目前這張會被中止…", "info")

    def _worker(self, sources, scale):
        """背景執行緒：只呼叫 upscaler，事件丟回 msg_queue"""
        try:
            with upscaler.BatchLock(LOCK_PATH):
                settings = upscaler.engine_settings(TOOL_DIR, scale)
                engine = upscaler.make_engine(TOOL_DIR, scale, LOG_DIR / 'engine.log', self.stop_event)
                upscaler.run_batch(
                    sources, scale, records_path=RECORDS_PATH, log_dir=LOG_DIR, settings=settings,
                    engine=engine, on_event=lambda kind, data: self.msg_queue.put(("batch", (kind, data))),
                    stop_event=self.stop_event, initial_rate=initial_rate(self.settings, scale))
        except RuntimeError as exc:
            if 'already running' in str(exc):
                self.msg_queue.put(("status", ("已有另一批正在執行，請等它結束", "error")))
            else:
                self.msg_queue.put(("status", (f"發生錯誤：{exc}", "error")))
        except Exception as exc:
            self.msg_queue.put(("status", (f"發生錯誤：{exc}", "error")))
        finally:
            self.msg_queue.put(("idle", scale))

    # ---- 執行緒安全 UI 更新 ----

    def _set_status(self, msg: str, level: str = "info"):
        colors = {"info": "gray", "success": "#2E7D32", "error": "#C62828"}
        self._status_bar.config(text=msg, foreground=colors.get(level, "gray"))

    def _handle_batch(self, kind, data):
        if kind == 'start':
            iid = str(data['index'])
            self.tree.set(iid, "status", STATUS_LABELS['running'])
            self.tree.see(iid)
            self.current_label.config(text=f"處理中：{data['source'].name}")
        elif kind == 'result':
            index, record = data['index'], data['record']
            self.tree.set(str(index), "status", STATUS_LABELS[record['status']])
            size = self.sizes[index]
            if size:
                self._done_pixels += size[0] * size[1] * self.scale_var.get() ** 2
            self.progress_bar["value"] = self._done_pixels
        elif kind == 'progress':
            eta = upscaler.format_duration(data['remaining_seconds'])
            self.progress_label.config(text=f"已完成 {data['done']}/{data['total']}，剩{eta}")
        elif kind == 'finished':
            counts = data['counts']
            self.failures = data['failures']
            if data['rate']:
                rates = dict(self.settings.get('rates', {}))
                rates[str(self.scale_var.get())] = round(data['rate'], 1)
                self._save_settings(rates=rates)
            summary = f"完成 {counts['done']}、跳過 {counts['skipped']}、失敗 {counts['failed']}"
            if data['stopped']:
                self._set_status("已停止，下次按開始會從沒完成的接著跑。" + summary, "info")
            elif data['aborted']:
                self._set_status(f"連續 {upscaler.MAX_ENGINE_FAILURES} 張放大程式出錯，可能是顯卡／驅動問題，已停止。"
                                 + summary, "error")
            elif counts['failed']:
                self._set_status(summary, "error")
            else:
                self._set_status(summary, "success")
            if self.failures:
                self.btn_failures.pack(side="left", ipady=6, padx=(8, 0))
            self.current_label.config(text="")

    def _poll_queue(self):
        """每 100ms 從 queue 拉訊息更新 UI（主執行緒安全）"""
        try:
            while True:
                msg_type, data = self.msg_queue.get_nowait()
                if msg_type == "batch":
                    self._handle_batch(*data)
                elif msg_type == "status":
                    self._set_status(*data)
                elif msg_type == "scanned":
                    self._set_sources(*data)
                    self._set_controls(True)
                    self._set_status(f"找到 {len(data[0])} 張圖片", "info")
                elif msg_type == "scan_error":
                    self._set_controls(True)
                    self._refresh_list()
                    self._set_status(f"掃描失敗：{data}", "error")
                elif msg_type == "idle":
                    self.is_running = False
                    self._set_controls(True)
                    self.btn_stop.config(state="disabled")
                    self._refresh_list()
                    if self._closing:
                        self.root.destroy()
                        return
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)


def main():
    # pythonw 沒有主控台，sys.stdout 是 None
    if sys.stdout is not None:
        show_cth_banner()
    root = tk.Tk()
    root.attributes("-topmost", True)   # 暫時置頂
    root.update()
    root.attributes("-topmost", False)
    ToolApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
