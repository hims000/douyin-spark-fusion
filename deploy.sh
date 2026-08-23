#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "请以 root 运行：sudo bash deploy.sh"
  exit 1
fi

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SERVICE_DIR/.venv"
UNIT_DST="/etc/systemd/system/douyin-spark.service"

echo "=============================================="
echo "  Douyin Spark Fusion - 一键部署脚本"
echo "=============================================="

# ── 1. 检查系统依赖 ──────────────────────────────────────────
echo ""
echo "==> [1/10] 检查系统依赖"
MISSING=()
for dep in python3 pip3 git curl; do
  if ! command -v "$dep" &>/dev/null; then
    MISSING+=("$dep")
  fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
  echo "缺少依赖: ${MISSING[*]}，尝试安装..."
  if command -v apt-get &>/dev/null; then
    apt-get update -y
    apt-get install -y python3 python3-venv python3-pip git curl
  elif command -v yum &>/dev/null; then
    yum install -y python3 python3-pip git curl
  elif command -v dnf &>/dev/null; then
    dnf install -y python3 python3-pip git curl
  else
    echo "错误: 无法自动安装依赖，请手动安装: ${MISSING[*]}"
    exit 1
  fi
else
  echo "系统依赖已就绪"
fi

# ── 2. 创建虚拟环境 ──────────────────────────────────────────
echo ""
echo "==> [2/10] 创建 Python 虚拟环境"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
  echo "虚拟环境已创建: $VENV"
else
  echo "虚拟环境已存在: $VENV"
fi
source "$VENV/bin/activate"

# ── 3. 安装 Python 依赖 ──────────────────────────────────────
echo ""
echo "==> [3/10] 安装 Python 依赖"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$SERVICE_DIR/requirements.txt"

# ── 4. 安装 Chromium ─────────────────────────────────────────
echo ""
echo "==> [4/10] 安装 Chromium"
"$VENV/bin/playwright" install chromium --with-deps 2>&1 | tail -5

# ── 5. 检查内存，按需创建 swap ───────────────────────────────
echo ""
echo "==> [5/10] 检查内存"
MEM_KB=$(grep MemTotal /proc/meminfo 2>/dev/null | awk '{print $2}' || echo 0)
MEM_MB=$((MEM_KB / 1024))
echo "当前内存: ${MEM_MB}MB"

if [ "$MEM_MB" -lt 1024 ]; then
  if ! swapon --show 2>/dev/null | grep -q 'swap'; then
    echo "内存不足 1GB 且无 swap，创建 2GB swap..."
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "swap 已创建并启用 (2GB)"
  else
    echo "已存在 swap，跳过创建"
  fi
else
  echo "内存充足 (>= 1GB)，无需 swap"
fi

# ── 6. 生成 AUTH_TOKEN ───────────────────────────────────────
echo ""
echo "==> [6/10] 生成 AUTH_TOKEN"
AUTH_TOKEN=$(openssl rand -hex 16)
echo "AUTH_TOKEN: $AUTH_TOKEN"

# ── 7. 配置 .env ─────────────────────────────────────────────
echo ""
echo "==> [7/10] 配置 .env"
if [ ! -f "$SERVICE_DIR/.env" ]; then
  if [ -f "$SERVICE_DIR/.env.example" ]; then
    cp "$SERVICE_DIR/.env.example" "$SERVICE_DIR/.env"
    echo "已从 .env.example 复制到 .env"
  else
    touch "$SERVICE_DIR/.env"
    echo "已创建空白 .env"
  fi
fi
if grep -q '^AUTH_TOKEN=' "$SERVICE_DIR/.env" 2>/dev/null; then
  sed -i "s/^AUTH_TOKEN=.*/AUTH_TOKEN=$AUTH_TOKEN/" "$SERVICE_DIR/.env"
else
  echo "AUTH_TOKEN=$AUTH_TOKEN" >> "$SERVICE_DIR/.env"
fi
echo "AUTH_TOKEN 已写入 $SERVICE_DIR/.env"

# ── 8. 创建 systemd 服务 ─────────────────────────────────────
echo ""
echo "==> [8/10] 创建 systemd 服务"
cat > "$UNIT_DST" << SYSTEMDUNIT
[Unit]
Description=Douyin Spark Fusion (auto streak)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$SERVICE_DIR
ExecStart=$VENV/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8000
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5
StandardOutput=append:$SERVICE_DIR/logs/service.log
StandardError=append:$SERVICE_DIR/logs/service.log

[Install]
WantedBy=multi-user.target
SYSTEMDUNIT

mkdir -p "$SERVICE_DIR/logs"

# ── 9. 启动并启用服务 ────────────────────────────────────────
echo ""
echo "==> [9/10] 启动并启用服务"
systemctl daemon-reload
systemctl enable --now douyin-spark
sleep 2
echo "服务状态:"
systemctl --no-pager --lines=5 status douyin-spark || true

# ── 10. 打印部署信息 ─────────────────────────────────────────
echo ""
echo "==> [10/10] 部署信息"
IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
echo "=============================================="
echo "  部署完成！"
echo "=============================================="
echo "  网页地址:    http://$IP:8000"
echo "  访问令牌:    $AUTH_TOKEN"
echo "  服务日志:    $SERVICE_DIR/logs/service.log"
echo "  令牌文件:    $SERVICE_DIR/.env"
echo "  服务管理:    systemctl [start|stop|restart|status] douyin-spark"
echo "=============================================="
echo "  接下来："
echo "  1. 打开网页 -> 登录 (admin / spark2024)"
echo "  2. 添加账号 -> 上传 Cookie 或 StorageState"
echo "  3. 同步好友列表 -> 创建定时任务"
echo "  4. 在「系统设置」中调整发送时间和频率"
echo "=============================================="