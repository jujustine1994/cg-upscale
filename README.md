/*  ================================  *\
 *                                    *
 *          C  T  H                   *
 *        created by CTH              *
 *                                    *
\*  ================================  */

規則檔: windows-tool.md
類型: Windows 工具

# CG Upscale

本機用 Real-ESRGAN（animevideov3 模型）把圖片放大 2／3／4 倍，放大版另存在原圖旁邊，原圖不動。改自先前專案的 `scripts/upscale_cg.py`。

## 執行方式

1. 雙擊 `CG Upscale啟動器.vbs`。第一次會開黑窗安裝 Python 與 Pillow，一直按 Enter 即可；之後只會出現程式視窗。
2. 選來源模式：單張圖片／多張圖片／資料夾／資料夾（含子資料夾）。單張按「選擇…」；其他模式按「加入…」，可以分好幾次加多批檔案或多個資料夾，「清空」重來。選完會自動掃描。
3. 選倍率 2／3／4 倍，「開始」左邊會用大字顯示「共 N 張，預估約…」。資料夾裡的圖片增減後，按「重新掃描」就好，不用重選。
4. 按「開始」。過程中顯示「已完成 12/80，剩約 11 分鐘」；可按「停止」，下次再按開始會跳過已完成的。
5. 跑完看底部狀態列；有失敗會出現「查看失敗」按鈕。

規則：

- 輸出檔名 `原檔名_x2.png`（倍率不同就是 `_x3`、`_x4`），一律 PNG，與原圖同資料夾。
- 檔名結尾是 `_x2`／`_x3`／`_x4` 的圖不會被當成來源，避免重複放大。
- 透明背景的圖：圖像本體用 AI 放大，透明度另外放大後合回，輸出仍是去背 PNG。
- 原圖、設定、輸出指紋（SHA-256）都一致才跳過；原圖改過或輸出損壞會重跑。
- 同資料夾有同名不同副檔名（如 `cg01.jpg` 與 `cg01.png`）時，只放大排序在前的那張，另一張標失敗並說明撞名，改名後再跑即可。
- 單張失敗會跳過繼續；連續 3 張放大程式出錯（多半是顯卡／驅動問題）才整批停下。
- 同時只能跑一批。
- 過夜執行：請接電，並把 Windows「插電時睡眠」設為「永不」，螢幕可以關。

紀錄位置：`cache/records.json`（跳過判斷）、`cache/settings.json`（上次選項與實測速度）、`logs/run-*.jsonl`（每批結果）、`logs/engine.log`（放大程式輸出）、`logs/app.log`（啟動器）。

除錯：直接執行 `powershell -NoProfile -ExecutionPolicy Bypass -File launcher.ps1` 會用黑窗顯示完整過程。

測試：`uv pip install -r requirements_test.txt --python "%USERPROFILE%\venvs\CG upscale\Scripts\python.exe"` 後執行 `"%USERPROFILE%\venvs\CG upscale\Scripts\python.exe" -m pytest tests`。

## 系統需求

- Windows 10/11
- 支援 Vulkan 的顯示卡（Intel／AMD／NVIDIA 皆可）
- Python 3.13（首次執行由 uv 自動安裝，裝在 `%USERPROFILE%\venvs\CG upscale`）

## 技術棧

- 語言：Python 3.13、PowerShell 5.1、VBScript（入口）
- 框架 / 套件：tkinter、Pillow、realesrgan-ncnn-vulkan（`tools/realesrgan/`）、uv、pytest（測試）

## .gitignore 必含項目

.env
*.pem
config.json
venv/
__pycache__/
*.pyc
cache/
logs/
.tmp/
