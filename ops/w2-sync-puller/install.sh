#!/bin/bash
# W2 索引同步 安装脚本（那豆侧运维执行，可重复执行）
#   - 拉取器：受限账号 w2sync，每小时 :23 从 COS 拉取并切换 current
#   - 导入器：root，每小时 :43 把 current 导入那豆检索库（在 api 容器里执行）
# 用法: sudo bash install.sh
set -e
cd "$(dirname "$0")"

APP_DIR="/opt/w2-sync-puller"
DATA_DIR="/var/lib/w2-index"
STATE_DIR="/var/lib/w2-import"
SERVICE_USER="w2sync"

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root / sudo 运行: sudo bash install.sh"
  exit 1
fi

echo "[1/7] 安装依赖 cos-python-sdk-v5"
if ! python3 -c "import qcloud_cos" 2>/dev/null; then
  pip3 install -q cos-python-sdk-v5 || pip3 install -q --break-system-packages cos-python-sdk-v5
fi

echo "[2/7] 创建受限服务账号 ${SERVICE_USER}"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "[3/7] 创建数据目录、导入状态目录、日志目录"
mkdir -p "$DATA_DIR/releases" "$APP_DIR" "$STATE_DIR" /var/log/w2-sync-puller
chown -R "$SERVICE_USER":"$SERVICE_USER" "$DATA_DIR" /var/log/w2-sync-puller
chown root:root "$STATE_DIR"
chmod 700 "$STATE_DIR"

echo "[4/7] 部署脚本到 ${APP_DIR}"
install -o root -g root -m 755 sync_puller.py "$APP_DIR/sync_puller.py"
install -o root -g root -m 700 w2_import_runner.py "$APP_DIR/w2_import_runner.py"

echo "[5/7] 生成配置文件（已存在则不覆盖）"
for f in config.env import.env; do
  if [ ! -f "$APP_DIR/$f" ]; then
    cp "$f.example" "$APP_DIR/$f"
    echo "    已生成 $APP_DIR/$f，请按手册填写"
  else
    echo "    $APP_DIR/$f 已存在，跳过"
  fi
  chown root:root "$APP_DIR/$f"
  chmod 600 "$APP_DIR/$f"
done

echo "[6/7] 安装 systemd service + timer"
cp sync-puller.service sync-puller.timer w2-import.service w2-import.timer /etc/systemd/system/
systemctl daemon-reload

echo "[7/7] 启用每小时定时"
systemctl enable --now sync-puller.timer
systemctl enable --now w2-import.timer

echo ""
echo "✅ 安装完成。下一步（见 README「装到正式服」）："
echo "   1. 编辑 $APP_DIR/config.env：COS 只读钥匙、桶名、钉钉 webhook"
echo "   2. 编辑 $APP_DIR/import.env：NADOU_DEPLOY_DIR"
echo "   3. systemctl start sync-puller.service && cat $DATA_DIR/status.json"
echo "   4. systemctl start w2-import.service && cat $STATE_DIR/state.json"
