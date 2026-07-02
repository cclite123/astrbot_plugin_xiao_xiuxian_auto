#!/usr/bin/env pwsh
# ============================================================
# deploy.ps1 — 小小修仙插件增量部署脚本
#
# 策略：
#   1. git archive 打包最新代码（仅含 git 跟踪文件）
#   2. scp 上传到服务器 /tmp
#   3. 服务器端 tar 解压，通过 --exclude 保护配置文件
#   4. 可选：远程重载插件
#
# 用法：
#   .\deploy.ps1                  # 默认部署
#   .\deploy.ps1 -Reload          # 部署并远程重载插件
#   .\deploy.ps1 -DryRun          # 仅展示将要同步的文件，不实际操作
# ============================================================

param(
    [switch]$Reload,
    [switch]$DryRun
)

# ── 连接配置 ──────────────────────────────────────────────
$SSH_HOST    = "ubuntu@81.71.44.7"
$SSH_PORT    = "50022"
$SSH_KEY     = "d:\Downloads\tx24.pem"
$REMOTE_DIR  = "/opt/astrbot/data/plugins/astrbot_plugin_xiao_xiuxian_auto"

# ── 工具路径（Git 自带 ssh/scp） ──────────────────────────
$GIT_DIR     = Split-Path (Split-Path (Get-Command git).Source)
$SSH         = Join-Path $GIT_DIR "usr\bin\ssh.exe" | Resolve-Path
$SCP         = Join-Path $GIT_DIR "usr\bin\scp.exe" | Resolve-Path

# ── 保护名单（永远不会被覆盖的文件/模式） ─────────────────
$PROTECTED = @(
    "config.json"                          # 主配置（含服务器个性化参数）
    "data/market_price_runtime_config.json" # 坊市价格运行时配置
    "data/inventory_ops_runtime_config.json"# 背包操作运行时配置
    "data/bounty_state.json"               # 悬赏状态数据
    "data/auto_alchemy_snapshot.json"      # 炼丹快照数据
    "data/market_prices_cache.json"        # 坊市价格缓存
    "data/__pycache__"                     # Python 字节码
    "__pycache__"                          # Python 字节码
    "*.pyc"                                # Python 编译文件
)

# ── 本地项目根目录 ────────────────────────────────────────
$PROJECT_DIR = $PSScriptRoot
if (-not $PROJECT_DIR) { $PROJECT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $PROJECT_DIR) { $PROJECT_DIR = "e:\xiaoxiuxian1.0.0" }

$ARCHIVE_NAME = "deploy_sync_$(Get-Date -Format 'yyyyMMdd_HHmmss').tar.gz"
$LOCAL_ARCHIVE = Join-Path $PROJECT_DIR $ARCHIVE_NAME
$REMOTE_TMP    = "/tmp/$ARCHIVE_NAME"

# ── 函数 ──────────────────────────────────────────────────
function Write-Step($msg)  { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "   OK: $msg" -ForegroundColor Green }
function Write-Skip($msg)  { Write-Host "   SKIP: $msg" -ForegroundColor Yellow }

# ── DryRun 模式：仅列出将同步的文件 ──────────────────────
if ($DryRun) {
    Write-Step "DryRun 模式 — 以下是将被同步的文件（git 跟踪文件）"
    git -C $PROJECT_DIR ls-files | ForEach-Object {
        $file = $_
        $isProtected = $false
        foreach ($pattern in $PROTECTED) {
            if ($file -eq $pattern -or $file -like $pattern -or $file.StartsWith("$pattern/")) {
                $isProtected = $true
                break
            }
        }
        if ($isProtected) {
            Write-Skip "$file  (受保护，不会覆盖)"
        } else {
            Write-Host "   SYNC: $file" -ForegroundColor White
        }
    }
    Write-Step "受保护文件汇总"
    foreach ($p in $PROTECTED) { Write-Skip $p }
    exit 0
}

# ── Step 1: 打包 ──────────────────────────────────────────
Write-Step "Step 1/4: 使用 git archive 打包最新代码"
git -C $PROJECT_DIR archive --format=tar.gz -o $LOCAL_ARCHIVE HEAD
if ($LASTEXITCODE -ne 0) { Write-Host "打包失败" -ForegroundColor Red; exit 1 }
$size = (Get-Item $LOCAL_ARCHIVE).Length
$sizeKB = [math]::Round(($size / 1024), 1)
$sizeStr = "${sizeKB} KB"
Write-Ok ("已打包 $ARCHIVE_NAME (" + $sizeStr + ")")

# ── Step 2: 上传 ──────────────────────────────────────────
Write-Step "Step 2/4: SCP 上传到服务器"
& $SCP -i $SSH_KEY -P $SSH_PORT -o StrictHostKeyChecking=no $LOCAL_ARCHIVE "${SSH_HOST}:${REMOTE_TMP}"
if ($LASTEXITCODE -ne 0) { Write-Host "上传失败" -ForegroundColor Red; exit 1 }
Write-Ok "已上传到 $REMOTE_TMP"

# ── Step 3: 解压（排除受保护文件） ────────────────────────
Write-Step "Step 3/4: 服务器端解压（保护配置文件）"

$excludeArgs = ($PROTECTED | ForEach-Object { "--exclude=$_" }) -join ' '
$remoteCmd = "cd $REMOTE_DIR ; sudo tar xzf $REMOTE_TMP $excludeArgs ; rm -f $REMOTE_TMP ; echo EXTRACT_OK"

$result = & $SSH -i $SSH_KEY -p $SSH_PORT -o StrictHostKeyChecking=no $SSH_HOST $remoteCmd 2>&1
if ("$result" -match "EXTRACT_OK") {
    Write-Ok "解压完成"
} else {
    Write-Host "   解压可能有问题: $result" -ForegroundColor Yellow
}

# 展示保护结果
Write-Step "受保护的文件（未被覆盖）"
foreach ($p in $PROTECTED) { Write-Skip $p }

# ── Step 4: 清理本地临时文件 ──────────────────────────────
Write-Step "Step 4/4: 清理本地临时文件"
Remove-Item $LOCAL_ARCHIVE -Force -ErrorAction SilentlyContinue
Write-Ok "已清理 $ARCHIVE_NAME"

# ── 可选：远程重载插件 ────────────────────────────────────
if ($Reload) {
    Write-Step "远程重载 AstrBot 插件..."
    $reloadCmd = 'touch ' + $REMOTE_DIR + '/main.py; echo RELOAD_DONE'
    $reloadResult = & $SSH -i $SSH_KEY -p $SSH_PORT -o StrictHostKeyChecking=no $SSH_HOST $reloadCmd 2>&1
    if ("$reloadResult" -match "API_RELOAD_NOT_AVAILABLE") {
        Write-Skip "API 重载不可用，请手动在 AstrBot WebUI 中重载插件"
    } else {
        Write-Ok "已触发插件重载"
    }
}

# ── 完成 ──────────────────────────────────────────────────
Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  部署完成！" -ForegroundColor Green
Write-Host "  目标: ${SSH_HOST}:${REMOTE_DIR}" -ForegroundColor Gray
Write-Host "========================================`n" -ForegroundColor Green
