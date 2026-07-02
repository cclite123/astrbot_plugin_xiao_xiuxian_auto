#!/usr/bin/env bash
# ============================================================
# deploy.sh — 小小修仙插件增量部署脚本（Linux / macOS）
#
# 策略:
#   1. git archive 打包最新代码
#   2. scp 上传到服务器 /tmp
#   3. tar --exclude 解压，保护配置文件
#
# 用法:
#   ./deploy.sh -H user@ip -P 22 -k /path/to/key.pem -d /opt/astrbot/.../plugin_dir
#   ./deploy.sh -c deploy-config.json
#   ./deploy.sh -H user@ip ... --dry-run
#   ./deploy.sh -H user@ip ... --reload
# ============================================================

set -euo pipefail

# ── 受保护文件 ────────────────────────────────────────────
PROTECTED=(
    "config.json"
    "data/market_price_runtime_config.json"
    "data/inventory_ops_runtime_config.json"
    "data/bounty_state.json"
    "data/auto_alchemy_snapshot.json"
    "data/market_prices_cache.json"
    "data/__pycache__"
    "__pycache__"
)

# ── 默认值 ────────────────────────────────────────────────
SSH_HOST=""
SSH_PORT="22"
SSH_KEY=""
REMOTE_DIR=""
RELOAD=false
DRY_RUN=false

# ── 参数解析 ──────────────────────────────────────────────
usage() {
    cat <<EOF
用法:
  ./deploy.sh -H <user@ip> [-P <port>] -k <key-path> -d <remote-dir> [--reload] [--dry-run]
  ./deploy.sh -c deploy-config.json

参数:
  -H, --host       SSH 目标地址   (如 ubuntu@81.71.44.7)
  -P, --port       SSH 端口       (默认 22)
  -k, --key        SSH 私钥路径   (如 ~/.ssh/server.pem)
  -d, --remote-dir 插件远程目录   (如 /opt/astrbot/data/plugins/astrbot_plugin_xiao_xiuxian_auto)
  -c, --config     从 JSON 文件读取参数
      --reload     部署后远程重载插件
      --dry-run    仅预览，不执行
  -h, --help       显示帮助
EOF
    exit 1
}

# 从 JSON 配置文件加载
load_config() {
    local file="$1"
    if command -v jq &>/dev/null; then
        SSH_HOST=$(jq -r '.host // empty' "$file")
        SSH_PORT=$(jq -r '.port // "22"' "$file")
        SSH_KEY=$(jq -r '.key // empty' "$file")
        REMOTE_DIR=$(jq -r '.remote_dir // empty' "$file")
    elif command -v python3 &>/dev/null; then
        SSH_HOST=$(python3 -c "import json; print(json.load(open('$file')).get('host',''))")
        SSH_PORT=$(python3 -c "import json; print(json.load(open('$file')).get('port','22'))")
        SSH_KEY=$(python3 -c "import json; print(json.load(open('$file')).get('key',''))")
        REMOTE_DIR=$(python3 -c "import json; print(json.load(open('$file')).get('remote_dir',''))")
    else
        echo "错误: 解析 JSON 需要 jq 或 python3" >&2
        exit 1
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -H|--host)       SSH_HOST="$2";    shift 2 ;;
        -P|--port)       SSH_PORT="$2";    shift 2 ;;
        -k|--key)        SSH_KEY="$2";     shift 2 ;;
        -d|--remote-dir) REMOTE_DIR="$2";  shift 2 ;;
        -c|--config)     load_config "$2"; shift 2 ;;
        --reload)        RELOAD=true;      shift ;;
        --dry-run)       DRY_RUN=true;     shift ;;
        -h|--help)       usage ;;
        *)               echo "未知参数: $1"; usage ;;
    esac
done

# ── 参数校验 ──────────────────────────────────────────────
if [[ -z "$SSH_HOST" || -z "$SSH_KEY" || -z "$REMOTE_DIR" ]]; then
    usage
fi

# ── 工具检查 ──────────────────────────────────────────────
for cmd in git ssh scp tar; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "错误: 未找到 $cmd，请先安装。" >&2
        exit 1
    fi
done

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
ARCHIVE_NAME="deploy_sync_$(date +%Y%m%d_%H%M%S).tar.gz"
LOCAL_ARCHIVE="/tmp/$ARCHIVE_NAME"
REMOTE_TMP="/tmp/$ARCHIVE_NAME"

# ── 颜色输出 ──────────────────────────────────────────────
cyan()  { printf '\033[36m>> %s\033[0m\n' "$*"; }
green() { printf '\033[32m   OK: %s\033[0m\n' "$*"; }
yellow(){ printf '\033[33m   SKIP: %s\033[0m\n' "$*"; }

# ── DryRun ────────────────────────────────────────────────
if $DRY_RUN; then
    cyan "DryRun - 以下文件将被同步"
    git -C "$PROJECT_DIR" ls-files | while read -r f; do
        hit=false
        for p in "${PROTECTED[@]}"; do
            if [[ "$f" == "$p" || "$f" == "$p"/* ]]; then hit=true; break; fi
        done
        if $hit; then yellow "$f  (protected)"
        else echo "   SYNC: $f"; fi
    done
    echo ""
    cyan "Protected files (will NOT be overwritten)"
    for p in "${PROTECTED[@]}"; do yellow "$p"; done
    exit 0
fi

# ── Step 1: 打包 ──────────────────────────────────────────
cyan "Step 1/4: git archive 打包"
git -C "$PROJECT_DIR" archive --format=tar.gz -o "$LOCAL_ARCHIVE" HEAD
sz=$(du -k "$LOCAL_ARCHIVE" | cut -f1)
green "已打包 (${sz} KB)"

# ── Step 2: 上传 ──────────────────────────────────────────
cyan "Step 2/4: SCP 上传"
scp -i "$SSH_KEY" -P "$SSH_PORT" -o StrictHostKeyChecking=no "$LOCAL_ARCHIVE" "${SSH_HOST}:${REMOTE_TMP}"
green "已上传到 ${SSH_HOST}:${REMOTE_TMP}"

# ── Step 3: 解压（排除受保护文件） ───────────────────────
cyan "Step 3/4: 服务器端解压（保护配置文件）"
exclude_args=""
for p in "${PROTECTED[@]}"; do
    exclude_args="$exclude_args --exclude=$p"
done

ssh -i "$SSH_KEY" -p "$SSH_PORT" -o StrictHostKeyChecking=no "$SSH_HOST" \
    "cd $REMOTE_DIR && sudo tar xzf $REMOTE_TMP $exclude_args && rm -f $REMOTE_TMP && echo EXTRACT_OK"

cyan "受保护文件（未被覆盖）"
for p in "${PROTECTED[@]}"; do yellow "$p"; done

# ── Step 4: 清理 ──────────────────────────────────────────
cyan "Step 4/4: 清理本地临时文件"
rm -f "$LOCAL_ARCHIVE"
green "清理完成"

# ── 可选: 远程重载 ────────────────────────────────────────
if $RELOAD; then
    cyan "远程重载插件..."
    ssh -i "$SSH_KEY" -p "$SSH_PORT" -o StrictHostKeyChecking=no "$SSH_HOST" \
        "touch ${REMOTE_DIR}/main.py && echo RELOAD_DONE" || true
    green "已触发重载（请在 AstrBot WebUI 确认）"
fi

# ── 完成 ──────────────────────────────────────────────────
echo ""
printf '\033[32m========================================\033[0m\n'
printf '\033[32m  Deploy complete!\033[0m\n'
printf '\033[90m  Target: %s:%s\033[0m\n' "$SSH_HOST" "$REMOTE_DIR"
printf '\033[32m========================================\033[0m\n'
