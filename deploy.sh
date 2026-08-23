#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "请以 root 运行：sudo bash deploy.sh"
  exit 1
fi

SERVICE_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SERVICE_DIR/.venv"
UNIT_SRC="$SERVICE_DIR/fusion-spark.service"
UNIT_DST="/etc/systemd/system/fusion-spark.service"

echo "==> 安装系统依赖"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx

echo "==> 创建 Python 虚拟环境"
if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi

echo "==> 安装 Python 依赖"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$SERVICE_DIR/requirements.txt"

echo "==> 安装 Chromium"
"$VENV/bin/playwright" install --with-deps chromium

echo "==> 配置 2G 交换空间"
if ! swapon --show | grep -q 'swap'; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "swap 已创建并启用"
else
  echo "检测到已有 swap，跳过"
fi

echo "==> 设置时区为 Asia/Shanghai"
timedatectl set-timezone Asia/Shanghai || echo "无法设置时区（容器环境可忽略）"

echo "==> 初始化数据目录"
mkdir -p "$SERVICE_DIR/data"

echo "==> 生成访问令牌"
if [ ! -f "$SERVICE_DIR/.env" ]; then
  TOKEN="$(head -c 24 /dev/urandom | sha256sum | head -c 32)"
  cat > "$SERVICE_DIR/.env" <<EOF
AUTH_TOKEN=$TOKEN
PORT=8000
HOST=0.0.0.0
HEADLESS=true
ALLOW_REGISTRATION=true
EOF
fi
TOKEN_VALUE="$(grep '^AUTH_TOKEN=' "$SERVICE_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r\n' || echo '')"
if [ -z "$TOKEN_VALUE" ]; then
  TOKEN_VALUE="$(head -c 24 /dev/urandom | sha256sum | head -c 32)"
  echo "AUTH_TOKEN=$TOKEN_VALUE" >> "$SERVICE_DIR/.env"
fi

echo "==> 安装 systemd 服务"
cat > "$UNIT_DST" << SYSTEMDUNIT
[Unit]
Description=Douyin Spark Fusion (auto streak)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$SERVICE_DIR
ExecStart=$VENV/bin/python app.py
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SYSTEMDUNIT
systemctl daemon-reload
systemctl enable --now fusion-spark
sleep 2
systemctl --no-pager --lines=5 status fusion-spark || true

echo "==> 配置 Nginx 反向代理"
cat > /etc/nginx/sites-available/fusion-spark << NGINXCONF
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        client_max_body_size 50m;
    }
}
NGINXCONF
ln -sf /etc/nginx/sites-available/fusion-spark /etc/nginx/sites-enabled/fusion-spark
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx || echo "Nginx 配置测试失败，跳过 Nginx 设置"

IP="$(hostname -I | awk '{print $1}')"
echo ""
echo "======================================================"
echo "部署完成！"
echo "网页地址: http://$IP"
echo "访问令牌: $TOKEN_VALUE"
echo "默认管理员: admin / spark2024"
echo "令牌保存在: $SERVICE_DIR/.env"
echo "======================================================"
echo "接下来："
echo "1. 打开网页 -> 登录 (admin / spark2024)"
echo "2. 添加账号 -> 上传 Cookie 或 StorageState"
echo "3. 同步好友列表 -> 创建定时任务"
echo "4. 在「系统设置」中调整发送时间和频率"