
#!/usr/bin/env python3
"""
使用示例脚本 - 测试架构后缀处理
"""

from aliyun_image_sync import DockerImageSync

def example_usage():
    # 目标私有仓库
    target_registry = "10.28.1.194:5000"
    
    # 要同步的镜像列表（包含架构后缀）
    images = [
        "registry.k8s.io/coredns/coredns:v1.11.1-amd64",
        "registry.k8s.io/kube-apiserver:v1.30.0-amd64",
        "registry.k8s.io/kube-controller-manager:v1.30.0-amd64",
        "registry.k8s.io/kube-scheduler:v1.30.0-amd64",
        "registry.k8s.io/kube-proxy:v1.30.0-amd64",
        "registry.k8s.io/pause:3.9-amd64",
        "registry.k8s.io/etcd:3.5.12-0-amd64",  # 这个没有架构后缀
    ]
    
    # 创建同步器（默认去除架构后缀）
    sync = DockerImageSync(target_registry, remove_arch_suffix=True)
    
    # 批量同步
    success = sync.batch_sync(images, cleanup=True)
    
    if success:
        print("所有镜像同步成功!")
    else:
        print("部分镜像同步失败!")

if __name__ == "__main__":
    example_usage()