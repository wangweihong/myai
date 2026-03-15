#!/bin/bash
set -e  # 遇到错误立即退出

# 检查是否以 root 身份运行（因为需要写入系统目录）
if [ "$EUID" -ne 0 ]; then
    echo "请使用 sudo 或以 root 身份运行此脚本。"
    exit 1
fi

echo "开始配置 Docker 动态代理支持..."

# --- 1. 创建 systemd drop-in 配置文件 ---
DOCKER_SERVICE_D="/etc/systemd/system/docker.service.d"
DROPIN_CONF="$DOCKER_SERVICE_D/http-proxy.conf"
PROXY_FILE="/etc/default/docker-proxy"   # 环境变量文件

mkdir -p "$DOCKER_SERVICE_D"

cat > "$DROPIN_CONF" <<EOF
[Service]
EnvironmentFile=-$PROXY_FILE
EOF

echo "✓ 已创建 systemd drop-in 配置: $DROPIN_CONF"

# --- 2. 生成独立的更新脚本 ---
UPDATE_SCRIPT="/usr/local/bin/update-docker-proxy.sh"

cat > "$UPDATE_SCRIPT" <<'EOF'
#!/bin/bash
set -e

# 检查 root 权限
if [ "$EUID" -ne 0 ]; then
    echo "请使用 sudo 或以 root 身份运行此脚本。"
    echo "提示：如果需要传递环境变量，请使用 sudo -E 选项。"
    exit 1
fi

PROXY_FILE="/etc/default/docker-proxy"

# 清空或创建代理配置文件
> "$PROXY_FILE"

# 将当前环境变量中的代理设置写入文件（仅当变量非空时）
if [ -n "$HTTP_PROXY" ]; then
    echo "HTTP_PROXY=\"$HTTP_PROXY\"" >> "$PROXY_FILE"
fi
if [ -n "$HTTPS_PROXY" ]; then
    echo "HTTPS_PROXY=\"$HTTPS_PROXY\"" >> "$PROXY_FILE"
fi
if [ -n "$NO_PROXY" ]; then
    echo "NO_PROXY=\"$NO_PROXY\"" >> "$PROXY_FILE"
fi

# 显示当前配置
if [ -s "$PROXY_FILE" ]; then
    echo "已启用 Docker 代理，配置如下："
    cat "$PROXY_FILE"
else
    echo "已禁用 Docker 代理（配置文件为空）"
fi

# 重新加载 systemd 并重启 Docker
systemctl daemon-reload
systemctl restart docker

echo "Docker 代理配置已更新并生效。"
EOF

chmod +x "$UPDATE_SCRIPT"
echo "✓ 已生成更新脚本: $UPDATE_SCRIPT"

# --- 3. 完成提示 ---
echo ""
echo "========================================================"
echo "安装完成！使用方法："
echo ""
echo "1. 当需要启用代理时，先设置环境变量，然后运行："
echo "   export HTTP_PROXY=http://你的代理地址:端口"
echo "   export HTTPS_PROXY=http://你的代理地址:端口"
echo "   export NO_PROXY=localhost,127.0.0.1"
echo "   sudo -E $UPDATE_SCRIPT"
echo ""
echo "2. 当需要禁用代理时，直接运行："
echo "   sudo $UPDATE_SCRIPT"
echo ""
echo "注意："
echo "- 使用 sudo -E 可以保留当前用户的环境变量。"
echo "- 重启 Docker 会停止所有容器，请确保已做好相应准备。"
echo "========================================================"