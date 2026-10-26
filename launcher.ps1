# CG Upscale 啟動器
# 兩種跑法：
#   雙擊 CG Upscale啟動器.vbs → 帶 -Silent，全程不開黑窗，主程式用 pythonw 跑
#   直接跑 powershell -File launcher.ps1 → 一般模式，黑窗顯示完整過程（首次安裝、除錯用）
param([switch]$Silent)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$host.UI.RawUI.WindowTitle = "CG Upscale"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# ======================================
# venv 位置（刻意放在專案資料夾外，理由見 windows-tool.md「venv 位置」）
# 專案名直接沿用資料夾名；含空白，之後每一處都要加引號
# ======================================
$VenvPath   = Join-Path $env:USERPROFILE "venvs\CG upscale"
$VenvPython = Join-Path $VenvPath "Scripts\python.exe"

# 無主控台模式碰到需要使用者操作的情況（首次安裝、缺 uv），改開一般視窗重跑，
# 不然 Read-Host 會在看不見的視窗裡卡住
if ($Silent -and (-not (Get-Command uv -ErrorAction SilentlyContinue) -or -not (Test-Path $VenvPython))) {
    Start-Process powershell -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit 0
}

# ======================================
# 執行紀錄（必加，須放在 trap 之前，閃退才記得到）
# ======================================
$LogFile = Join-Path $ScriptDir "logs\app.log"
New-Item -ItemType Directory -Force (Split-Path $LogFile) | Out-Null
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)   # 不可用 Add-Content -Encoding UTF8，會寫 BOM（地雷十一）

function Write-Log {
    param([string]$Msg, [string]$Level = "INFO")
    $line = "[{0}] [{1,-5}] {2}`r`n" -f (Get-Date -Format "HH:mm:ss"), $Level, $Msg
    try { [System.IO.File]::AppendAllText($LogFile, $line, $Utf8NoBom) } catch {}   # 不持有 handle（地雷十）
}

function Write-LogHeader {
    param([string]$Msg)
    $line = "=== {0} {1} ===`r`n" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Msg
    try { [System.IO.File]::AppendAllText($LogFile, $line, $Utf8NoBom) } catch {}
}

# 畫面上的「現在正在做什麼」提示，帶時間戳；不寫 log（log 已有自己的時間戳）。
function Write-Step {
    param([string]$Msg, [string]$Color = "Gray")
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $Msg" -ForegroundColor $Color
}

function Show-CTHBanner {
    $e = [char]27
    $b = "${e}[90m"    # 邊框：深灰
    $c = "${e}[96m"    # CTH 字母：亮青
    $y = "${e}[93m"    # 署名：金黃
    $r = "${e}[0m"     # reset

    Write-Host ""
    Write-Host "${b}/*  ================================  *\${r}"
    Write-Host "${b} *                                    *${r}"
    Write-Host "${b} *    ${c}██████╗████████╗██╗  ██╗${b}        *${r}"
    Write-Host "${b} *   ${c}██╔════╝   ██║   ██║  ██║${b}        *${r}"
    Write-Host "${b} *   ${c}██║        ██║   ███████║${b}        *${r}"
    Write-Host "${b} *   ${c}██║        ██║   ██╔══██║${b}        *${r}"
    Write-Host "${b} *   ${c}╚██████╗   ██║   ██║  ██║${b}        *${r}"
    Write-Host "${b} *    ${c}╚═════╝   ╚═╝   ╚═╝  ╚═╝${b}        *${r}"
    Write-Host "${b} *                                    *${r}"
    Write-Host "${b} *          ${y}created by CTH${b}            *${r}"
    Write-Host "${b}\*  ================================  */${r}"
    Write-Host ""
}

# 無主控台模式沒有畫面可看，錯誤改用彈窗告知
function Show-Popup {
    param([string]$Msg)
    try { (New-Object -ComObject WScript.Shell).Popup($Msg, 0, "CG Upscale", 16) | Out-Null } catch {}
}

Write-LogHeader "App start"

# 攔截所有未預期例外，防止視窗直接閃退
trap {
    Write-Log "[CRASH] $($_.Exception.Message) @ line $($_.InvocationInfo.ScriptLineNumber)" "FATAL"
    if ($Silent) {
        Show-Popup "啟動失敗：$($_.Exception.Message)`n`n已記錄至 logs\app.log，請交給開發者或 AI 查看。"
        exit 1
    }
    Write-Host ""
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Red
    Write-Host "[CRASH] 意外錯誤，程式無法繼續執行" -ForegroundColor Red
    Write-Host ""
    Write-Host "  錯誤訊息：$($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "  發生位置：$($_.InvocationInfo.ScriptLineNumber) 行" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  已記錄至 logs\app.log，請連同此畫面回報給開發者。" -ForegroundColor White
    Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Red
    Read-Host "按 Enter 關閉"
    exit 1
}

if (-not $Silent) {
    Clear-Host
    Show-CTHBanner
    Write-Host "[INFO] Starting CG Upscale..." -ForegroundColor Green
    Write-Host ""
}

# ======================================
# [1/2] 檢查 uv
#
# ⚠ 只檢查 uv，不檢查系統 Python——uv 自己就會下載 Python。
# ======================================
Write-Host "[1/2] 檢查 uv 套件管理工具..." -ForegroundColor Cyan
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Log "uv not found, installing" "WARN"
    Write-Step "找不到 uv，開始下載安裝程式（astral.sh）..." "Yellow"
    # 較舊的 Win10 上 PowerShell 5.1 可能仍預設 TLS 1.0/1.1，astral.sh 只收 1.2 以上
    try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
    try {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    } catch {
        Write-Log "uv download failed -> $($_.Exception.GetType().Name)" "ERROR"
    }
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "User") + ";" + $env:PATH
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
        Write-Log "uv install failed" "ERROR"
        Write-Host "[ERROR] uv 安裝失敗，多半是連不上網路。請確認網路連線後關閉視窗，" -ForegroundColor Red
        Write-Host "        重新點兩下啟動檔再試一次。" -ForegroundColor Red
        Read-Host "按 Enter 關閉"; exit 1
    }
    $uvVer = uv --version
    Write-Host "[OK] uv 安裝完成。" -ForegroundColor Green
} else {
    $uvVer = uv --version
    Write-Host "[OK] $uvVer 已安裝。" -ForegroundColor Green
}

# ======================================
# [2/2] 檢查虛擬環境
# ======================================
Write-Host "[2/2] 檢查虛擬環境..." -ForegroundColor Cyan
# 用 python.exe 判斷而不是資料夾：資料夾可能存在但裡面是空的或壞的
if (-not (Test-Path $VenvPython)) {
    Write-Host ""
    Write-Host "  ============================================" -ForegroundColor Cyan
    Write-Host "    CG Upscale - 首次安裝說明" -ForegroundColor Cyan
    Write-Host "  ============================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  接下來程式會自動幫你安裝以下東西：" -ForegroundColor White
    Write-Host ""
    Write-Host "    1. Python 本體與虛擬環境（venv）" -ForegroundColor Yellow
    Write-Host "       電腦沒裝過 Python 也沒關係，會自動下載（約 20MB）" -ForegroundColor Gray
    Write-Host "       虛擬環境讓這個工具有獨立乾淨的執行空間，不影響電腦其他程式" -ForegroundColor Gray
    Write-Host ""
    Write-Host "    2. Pillow" -ForegroundColor Yellow
    Write-Host "       讀寫圖片用的套件：讀取尺寸、處理透明背景、存成 PNG" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  放大用的 Real-ESRGAN 程式已經放在 tools\realesrgan，不需要另外下載。" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  全程只需要一直按 Enter 同意即可。" -ForegroundColor Green
    Write-Host "  看不懂的話，可以把這段說明貼給 AI 詢問。" -ForegroundColor Green
    Write-Host ""
    Write-Host "  ============================================" -ForegroundColor Cyan
    Write-Host ""
    $ans = Read-Host "[WARNING] 找不到虛擬環境，現在建立並安裝套件？[Y/n] - 直接按 Enter 代表同意"
    if ($ans -eq "" -or $ans -ieq "Y") {
        Write-Step "下載/建立 Python 虛擬環境中（電腦若沒有 Python 會自動下載，約 20MB）..."
        New-Item -ItemType Directory -Force (Split-Path $VenvPath) | Out-Null
        uv venv "$VenvPath" --python 3.13
        if ($LASTEXITCODE -ne 0) {
            Write-Log "venv creation failed (uv venv exit $LASTEXITCODE)" "ERROR"
            Write-Host "[ERROR] 建立虛擬環境失敗，多半是下載 Python 時連不上網路。" -ForegroundColor Red
            Write-Host "        請確認網路連線後關閉視窗，重新點兩下啟動檔再試一次。" -ForegroundColor Red
            Read-Host "按 Enter 關閉"; exit 1
        }
        Write-Step "安裝套件中（以下為 uv 逐一顯示的套件名稱與下載進度）..."
        uv pip install -r requirements.txt --python "$VenvPython"
        if ($LASTEXITCODE -ne 0) {
            Write-Log "package install failed (uv pip install exit $LASTEXITCODE)" "ERROR"
            Write-Host "[ERROR] 套件安裝失敗，請確認網路連線後重新執行。" -ForegroundColor Red
            Read-Host "按 Enter 關閉"; exit 1
        }
        Write-Host "[OK] 套件安裝完成。" -ForegroundColor Green
    } else {
        Write-Host "已取消。" -ForegroundColor Gray; Read-Host "按 Enter 關閉"; exit 1
    }
} else {
    Write-Host "[OK] 虛擬環境已就緒，檢查套件更新..." -ForegroundColor Green
    # 清理損壞的 dist-info（METADATA 檔遺失時 uv 會拒絕安裝）
    $broken = Get-ChildItem (Join-Path $VenvPath "Lib\site-packages") -Directory -Filter "*dist-info" -ErrorAction SilentlyContinue | Where-Object {
        -not (Test-Path (Join-Path $_.FullName "METADATA"))
    }
    foreach ($dir in $broken) {
        Write-Host "[INFO] 清理損壞的套件資訊：$($dir.Name)" -ForegroundColor Yellow
        Remove-Item -Recurse -Force $dir.FullName
    }
    uv pip install -r requirements.txt --python "$VenvPython"
}

# 修正 venv 的假 pythonw.exe（地雷十三）
$VenvPythonW = Join-Path $VenvPath "Scripts\pythonw.exe"
if ((Get-FileHash $VenvPythonW).Hash -eq (Get-FileHash $VenvPython).Hash) {
    $pyHome = (Get-Content (Join-Path $VenvPath "pyvenv.cfg") | Where-Object { $_ -match '^home\s*=' } | Select-Object -First 1) -replace '^home\s*=\s*', ''
    $wLauncher = Join-Path $pyHome "Lib\venv\scripts\nt\venvwlauncher.exe"
    try {
        Copy-Item $wLauncher $VenvPythonW -Force -ErrorAction Stop
        Write-Log "Fixed venv pythonw.exe (replaced with venvwlauncher.exe)"
    } catch {
        Write-Log "Fix pythonw.exe failed ($($_.Exception.GetType().Name)), console may still appear" "WARN"
    }
}

. (Join-Path $VenvPath "Scripts\Activate.ps1")

$pyVer = (& "$VenvPython" --version 2>&1 | Out-String).Trim()
Write-Log "Environment ready | $pyVer | $uvVer"

if (-not $Silent) {
    Write-Host ""
    Write-Host "[START] 啟動中，請保持此視窗開啟..." -ForegroundColor Green
    Write-Host ""
}

# 主程式執行期間由它自己寫 log，launcher 不寫（避免搶 handle，地雷十）
if ($Silent) {
    # pythonw 沒有主控台：例外的 traceback 走 stderr，導到 logs\last_error.txt 才看得到
    $ErrFile = Join-Path $ScriptDir "logs\last_error.txt"
    $env:PYTHONIOENCODING = "utf-8"
    $proc = Start-Process -FilePath $VenvPythonW -ArgumentList "src\app.py" `
        -WorkingDirectory $ScriptDir -RedirectStandardError $ErrFile -PassThru
    $null = $proc.Handle   # 先抓 handle，不然結束後 ExitCode 讀不到
    $proc.WaitForExit()
    $exitCode = $proc.ExitCode
    if ((Get-Item $ErrFile -ErrorAction SilentlyContinue).Length -gt 0) {
        Write-Log "App wrote to stderr, see logs\last_error.txt" "WARN"
    } else {
        Remove-Item $ErrFile -ErrorAction SilentlyContinue
    }
} else {
    python src\app.py
    $exitCode = $LASTEXITCODE
}

if (Test-Path "src\__pycache__") { Remove-Item -Recurse -Force "src\__pycache__" }

if ($exitCode -ne 0) {
    Write-Log "app exited abnormally (exit code $exitCode)" "ERROR"
    if ($Silent) {
        Show-Popup "程式意外停止（代碼 $exitCode）。`n`n錯誤內容在 logs\last_error.txt，請交給開發者或 AI 查看。"
        exit 1
    }
    Write-Host ""
    Write-Host "[ERROR] 程式意外停止，請回報上方錯誤訊息。" -ForegroundColor Red
    Read-Host "按 Enter 關閉"
} elseif (-not $Silent) {
    Write-Host ""
    Write-Host "5 秒後自動關閉..." -ForegroundColor Gray
    Start-Sleep -Seconds 5
}
