#!/bin/bash
mkdir -p /opt/registry/config/
mkdir -p /opt/registry/certs/
mkdir -p /opt/registry/data/
cat <<EOF >/opt/registry/config/config.yml
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

openssl req -newkey rsa:2048 -nodes -keyout /opt/registry/certs/domain.key -x509 -days 3650 -out /opt/registry/certs/domain.crt \
          -subj "/C=CO/ST=STE/L=CY/O=OR/OU=ORG/CN=OME"



 docker run -d \
  --restart=always \
  -p 5000:5000 \
  --name registry \
  --mount type=bind,src=/opt/registry/data,dst=/var/lib/registry \
  --mount type=bind,src=/opt/registry/certs,dst=/certs \
  --mount type=bind,src=/opt/registry/config,dst=/etc/docker/registry/ \
   --env REGISTRY_HTTP_TLS_CERTIFICATE=/certs/domain.crt \
  --env REGISTRY_HTTP_TLS_KEY=/certs/domain.key \
  docker.io/library/registry:2