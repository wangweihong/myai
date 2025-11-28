#!/bin/bash
set -ex 

# 创建必要的目录
sudo mkdir -p /opt/registry/config/
sudo mkdir -p /opt/registry/certs/
sudo mkdir -p /opt/registry/data/

# 创建配置文件
sudo tee /opt/registry/config/config.yml > /dev/null <<'EOF'
version: 0.1
log:
  fields:
    service: registry
storage:
  # 允许删除镜像tag
  delete:
    enabled: true
  cache:
    blobdescriptor: inmemory
  filesystem:
    rootdirectory: /var/lib/registry
http:
  addr: :5000
  headers:
    X-Content-Type-Options: [nosniff]
health:
  storagedriver:
    enabled: true
    interval: 10s
    threshold: 3
EOF

# 生成SSL证书（注意：这里没有使用sudo）
sudo openssl req -newkey rsa:2048 -nodes -keyout /opt/registry/certs/domain.key -x509 -days 3650 -out /opt/registry/certs/domain.crt \
          -subj "/C=CO/ST=STE/L=CY/O=OR/OU=ORG/CN=OME"

# 修复证书权限问题
sudo chmod 644 /opt/registry/certs/domain.crt
sudo chmod 600 /opt/registry/certs/domain.key

# 停止并删除已存在的同名容器（如果存在）
sudo docker stop registry || true
sudo docker rm registry || true



# 修复：关键错误 - 将 "ocker" 改为 "docker"
sudo docker run -d \
  --restart=always \
  -p 5000:5000 \
  --name registry \
  --mount type=bind,src=/opt/registry/data,dst=/var/lib/registry \
  --mount type=bind,src=/opt/registry/certs,dst=/certs \
  --mount type=bind,src=/opt/registry/config,dst=/etc/docker/registry/ \
  --env REGISTRY_HTTP_TLS_CERTIFICATE=/certs/domain.crt \
  --env REGISTRY_HTTP_TLS_KEY=/certs/domain.key \
 registry.cn-hangzhou.aliyuncs.com/eazycloud/registry:2