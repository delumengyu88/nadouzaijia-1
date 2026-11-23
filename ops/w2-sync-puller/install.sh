#!/bin/bash
# W2 索引同步拉取器 安装脚本（那豆侧运维执行）
# 用法: sudo bash install.sh
set -e

APP_DIR="/opt/w2-sync-puller"
DATA_DIR="/var/lib/w2-index"
SERVICE_USER="w2sync"

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root / sudo 运行: sudo bash install.sh"
  exit 1
fi

echo "[1/6] 安装依赖 cos-python-sdk-v5"
pip3 install -q cos-python-sdk-v5 2>/dev/null || pip3 install -q cos-python-sdk-v5

echo "[2/6] 创建受限服务账号 ${SERVICE_USER}"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "[3/6] 创建数据目录 ${DATA_DIR} 与日志目录"
mkdir -p "$DATA_DIR/releases" "$APP_DIR" /var/log/w2-sync-puller
chown -R "$SERVICE_USER":"$SERVICE_USER" "$DATA_DIR" /var/log/w2-sync-puller

echo "[4/6] 部署脚本到 ${APP_DIR}"
cp sync_puller.py "$APP_DIR/"
chown "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR/sync_puller.py"
chmod 755 "$APP_DIR/sync_puller.py"

if [ ! -f "$APP_DIR/config.env" ]; then
  cp config.env.example "$APP_DIR/config.env"
  echo "    已生成 $APP_DIR/config.env，请填入 COS 只读凭证后执行下一步"
else
  echo "    $APP_DIR/config.env 已存在，跳过"
fi
chown root:root "$APP_DIR/config.env"
chmod 600 "$APP_DIR/config.env"

echo "[5/6] 安装 systemd service + timer"
cp sync-puller.service /etc/systemd/system/
cp sync-puller.timer   /etc/systemd/system/
systemctl daemon-reload

echo "[6/6] 启用每小时定时"
systemctl enable sync-puller.timer
systemctl start sync-puller.timer

echo ""
echo "✅ 安装完成。"
echo "   下一步：编辑 $APP_DIR/config.env 填入 COS 凭证 + 钉钉 webhook → systemctl restart sync-puller.timer"
echo "   手动重跑一次：systemctl start sync-puller.service"
echo "   查看状态：systemctl status sync-puller.timer && cat $DATA_DIR/status.json"
