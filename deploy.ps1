#!/usr/bin/env pwsh
<#
.SYNOPSIS
    小小修仙插件 — 增量部署脚本（Windows / PowerShell）

.DESCRIPTION
    通过 git archive + tar --exclude 实现安全的增量更新，
    自动保护服务器上的个性化配置文件不被覆盖。

.PARAMETER SshHost
    SSH 目标地址，格式: user@ip 或 user@hostname

.PARAMETER Port
    SSH 端口号（默认 22）

.PARAMETER Key
    SSH 私钥文件路径（如 D:\keys\server.pem）

.PARAMETER RemoteDir
    服务器上的 AstrBot 插件目录

.PARAMETER Reload
    部署完成后远程重载插件

.PARAMETER DryRun
    仅预览将要同步的文件，不执行实际操作

.PARAMETER Config
    从 JSON 配置文件读取连接参数（见下方示例）

.EXAMPLE
    # 交互式指定参数
    .\deploy.ps1 -SshHost ubuntu@81.71.44.7 -Port 50022 -Key D:\keys\server.pem `
                 -RemoteDir /opt/astrbot/data/plugins/astrbot_plugin_xiao_xiuxian_auto

    # 使用配置文件
    .\deploy.ps1 -Config .\deploy-config.json

    # 预览模式
    .\deploy.ps1 -SshHost ubuntu@81.71.44.7 -Port 50022 -Key D:\keys\server.pem `
                 -RemoteDir /opt/astrbot/data/plugins/astrbot_plugin_xiao_xiuxian_auto -DryRun

    # deploy-config.json 示例:
    {
        "host": "ubuntu@81.71.44.7",
        "port": "50022",
        "key":  "D:\\keys\\server.pem",
        "remote_dir": "/opt/astrbot/data/plugins/astrbot_plugin_xiao_xiuxian_auto"
    }
#>

param(
    [string]$SshHost   = "",
    [string]$Port      = "22",
    [string]$Key       = "",
    [string]$RemoteDir = "",
    [switch]$Reload,
    [switch]$DryRun,
    [string]$Config    = ""
)

# ── 从配置文件加载（如果指定了 -Config） ──────────────────
if ($Config -and (Test-Path $Config)) {
    $cfg = Get-Content $Config -Raw | ConvertFrom-Json
    if ($cfg.host)      { $SshHost   = $cfg.host }
    if ($cfg.port)      { $Port      = $cfg.port }
    if ($cfg.key)       { $Key       = $cfg.key }
    if ($cfg.remote_dir){ $RemoteDir = $cfg.remote_dir }
}

# ── 参数校验 ──────────────────────────────────────────────
if (-not $SshHost -or -not $Key -or -not $RemoteDir) {
    Write-Host @"

用法:
  .\deploy.ps1 -SshHost <user@ip> -Port <port> -Key <key-path> -RemoteDir <path>
  .\deploy.ps1 -Config deploy-config.json
  .\deploy.ps1 -SshHost <user@ip> ... -DryRun      # Preview

Parameters:
  -SshHost    SSH target   (e.g. ubuntu@81.71.44.7)
  -Port       SSH 端口     (默认 22)
  -Key        SSH 私钥路径 (如 D:\keys\server.pem)
  -RemoteDir  插件远程目录 (如 /opt/astrbot/data/plugins/astrbot_plugin_xiao_xiuxian_auto)
  -Reload     部署后重载插件
  -DryRun     仅预览，不实际操作
  -Config     从 JSON 文件读取上述参数

"@ -ForegroundColor Yellow
    exit 1
}

# ── 自动检测 SSH/SCP ──────────────────────────────────────
$SSH = $null; $SCP = $null

# 方式1：系统 PATH 中查找
$SSH = Get-Command ssh -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
$SCP = Get-Command scp -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source

# 方式2：Git 自带的 OpenSSH
if (-not $SSH -or -not $SCP) {
    $gitPath = Get-Command git -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
    if ($gitPath) {
        $gitUsrBin = Join-Path (Split-Path (Split-Path $gitPath)) "usr\bin"
        $gitSSH = Join-Path $gitUsrBin "ssh.exe"
        $gitSCP = Join-Path $gitUsrBin "scp.exe"
        if (-not $SSH -and (Test-Path $gitSSH)) { $SSH = $gitSSH }
        if (-not $SCP -and (Test-Path $gitSCP)) { $SCP = $gitSCP }
    }
}

if (-not $SSH -or -not $SCP) {
    Write-Host "错误: 未找到 ssh/scp。请安装 OpenSSH 客户端或 Git for Windows。" -ForegroundColor Red
    exit 1
}

# ── 受保护文件（永远不会被覆盖） ──────────────────────────
$PROTECTED = @(
    "config.json"
    "data/market_price_runtime_config.json"
    "data/inventory_ops_runtime_config.json"
    "data/bounty_state.json"
    "data/auto_alchemy_snapshot.json"
    "data/market_prices_cache.json"
    "data/__pycache__"
    "__pycache__"
    "*.pyc"
)

# ── 路径与临时文件 ────────────────────────────────────────
$PROJECT_DIR  = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
$ARCHIVE_NAME = "deploy_sync_$(Get-Date -Format 'yyyyMMdd_HHmmss').tar.gz"
$LOCAL_ARCHIVE = Join-Path $PROJECT_DIR $ARCHIVE_NAME
$REMOTE_TMP    = "/tmp/$ARCHIVE_NAME"

# ── 输出函数 ──────────────────────────────────────────────
function Write-Step($msg)  { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "   OK: $msg" -ForegroundColor Green }
function Write-Skip($msg)  { Write-Host "   SKIP: $msg" -ForegroundColor Yellow }

# ── DryRun 模式 ──────────────────────────────────────────
if ($DryRun) {
    Write-Step "DryRun - 以下文件将被同步（git 跟踪文件）"
    git -C $PROJECT_DIR ls-files | ForEach-Object {
        $file = $_
        $hit = $false
        foreach ($p in $PROTECTED) {
            if ($file -eq $p -or $file -like $p -or $file.StartsWith("$p/")) { $hit = $true; break }
        }
        if ($hit) { Write-Skip "$file  (protected)" }
        else      { Write-Host "   SYNC: $file" -ForegroundColor White }
    }
    Write-Step "Protected files (will NOT be overwritten)"
    foreach ($p in $PROTECTED) { Write-Skip $p }
    exit 0
}

# ── Step 1: git archive 打包 ─────────────────────────────
Write-Step "Step 1/4: git archive 打包"
git -C $PROJECT_DIR archive --format=tar.gz -o $LOCAL_ARCHIVE HEAD
if ($LASTEXITCODE -ne 0) { Write-Host "打包失败" -ForegroundColor Red; exit 1 }
$sz = [math]::Round(((Get-Item $LOCAL_ARCHIVE).Length / 1024), 1)
Write-Ok ("已打包 (${sz} KB)")

# ── Step 2: SCP 上传 ─────────────────────────────────────
Write-Step "Step 2/4: SCP 上传"
& $SCP -i $Key -P $Port -o StrictHostKeyChecking=no $LOCAL_ARCHIVE "${SshHost}:${REMOTE_TMP}"
if ($LASTEXITCODE -ne 0) { Write-Host "Upload failed" -ForegroundColor Red; exit 1 }
Write-Ok "Uploaded to ${SshHost}:${REMOTE_TMP}"

# ── Step 3: 服务器端解压（排除受保护文件） ───────────────
Write-Step "Step 3/4: 服务器端解压（保护配置文件）"
$excludeArgs = ($PROTECTED | ForEach-Object { "--exclude=$_" }) -join ' '
$remoteCmd = "cd $RemoteDir ; sudo tar xzf $REMOTE_TMP $excludeArgs ; rm -f $REMOTE_TMP ; echo EXTRACT_OK"

$result = & $SSH -i $Key -p $Port -o StrictHostKeyChecking=no $SshHost $remoteCmd 2>&1
if ("$result" -match "EXTRACT_OK") {
    Write-Ok "解压完成"
} else {
    Write-Host "   解压输出: $result" -ForegroundColor Yellow
}

Write-Step "受保护文件（未被覆盖）"
foreach ($p in $PROTECTED) { Write-Skip $p }

# ── Step 4: 清理 ─────────────────────────────────────────
Write-Step "Step 4/4: 清理本地临时文件"
Remove-Item $LOCAL_ARCHIVE -Force -ErrorAction SilentlyContinue
Write-Ok "清理完成"

# ── 可选：远程重载 ────────────────────────────────────────
if ($Reload) {
    Write-Step "远程重载插件..."
    $reloadCmd = 'touch ' + $RemoteDir + '/main.py; echo RELOAD_DONE'
    $rr = & $SSH -i $Key -p $Port -o StrictHostKeyChecking=no $SshHost $reloadCmd 2>&1
    if ("$rr" -match "RELOAD_DONE") { Write-Ok "已触发重载" }
    else { Write-Skip "请手动在 AstrBot WebUI 中重载插件" }
}

# ── 完成 ──────────────────────────────────────────────────
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Deploy complete!" -ForegroundColor Green
Write-Host ("  Target: " + $SshHost + ":" + $RemoteDir) -ForegroundColor Gray
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
