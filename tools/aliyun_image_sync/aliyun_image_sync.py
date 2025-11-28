#!/usr/bin/env python3
"""
Docker镜像同步脚本
用于从阿里云镜像仓库下载镜像，重新打标签后推送到私有仓库
支持自动去除 -amd64 和 -arm64 架构后缀
"""

import subprocess
import sys
import argparse
import logging
import re
from typing import List, Tuple

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class DockerImageSync:
    def __init__(self, target_registry: str, remove_arch_suffix: bool = True):
        """
        初始化
        
        Args:
            target_registry: 目标私有仓库地址，如 10.28.1.194:5000
            remove_arch_suffix: 是否去除架构后缀 (-amd64, -arm64)
        """
        self.target_registry = target_registry
        self.aliyun_registry = "registry.cn-hangzhou.aliyuncs.com/eazycloud"
        self.remove_arch_suffix = remove_arch_suffix
        
    def run_command(self, cmd: List[str]) -> bool:
        """
        执行shell命令
        
        Args:
            cmd: 命令列表
            
        Returns:
            bool: 是否执行成功
        """
        logger.debug(f"执行命令: {' '.join(cmd)}")
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.debug(f"命令输出: {result.stdout}")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"命令执行失败: {' '.join(cmd)}")
            logger.error(f"错误信息: {e.stderr}")
            return False
    
    def convert_image_name(self, original_image: str) -> str:
        """
        转换镜像名称格式
        
        Args:
            original_image: 原始镜像名，如 registry.k8s.io/coredns/coredns:1.0
            
        Returns:
            str: 阿里云镜像名
        """
        # 将斜杠替换为下划线
        ali_image = original_image.replace('/', '_')
        return f"{self.aliyun_registry}/{ali_image}"
    
    def process_image_tag(self, image_name: str) -> Tuple[str, bool]:
        """
        处理镜像tag，去除架构后缀
        
        Args:
            image_name: 原始镜像名
            
        Returns:
            Tuple[str, bool]: (处理后的镜像名, 是否进行了修改)
        """
        if not self.remove_arch_suffix:
            return image_name, False
            
        # 检查是否包含架构后缀
        arch_suffixes = ['-amd64', '-arm64']
        
        # 分离镜像名和tag
        if ':' in image_name:
            repo, tag = image_name.rsplit(':', 1)
            
            # 检查tag是否包含架构后缀
            modified = False
            for suffix in arch_suffixes:
                if tag.endswith(suffix):
                    # 去除架构后缀
                    new_tag = tag[:-len(suffix)]
                    logger.info(f"检测到架构后缀 '{suffix}'，将tag从 '{tag}' 修改为 '{new_tag}'")
                    return f"{repo}:{new_tag}", True
        else:
            # 没有tag的情况
            repo = image_name
            tag = "latest"
            
        return image_name, False
    
    def sync_image(self, original_image: str) -> bool:
        """
        同步单个镜像
        
        Args:
            original_image: 原始镜像名
            
        Returns:
            bool: 是否同步成功
        """
        logger.info(f"开始同步镜像: {original_image}")
        
        # 处理架构后缀
        cleaned_image, was_modified = self.process_image_tag(original_image)
        if was_modified:
            logger.info(f"清理架构后缀后镜像名: {cleaned_image}")
        
        # 1. 转换镜像名为阿里云格式
        ali_image = self.convert_image_name(original_image)
        logger.info(f"阿里云镜像名: {ali_image}")
        
        # 2. 从阿里云下载镜像
        logger.info("步骤1: 从阿里云下载镜像")
        pull_cmd = ["docker", "pull", ali_image]
        if not self.run_command(pull_cmd):
            logger.error(f"下载镜像失败: {ali_image}")
            return False
        
        # 3. 重新tag为清理后的镜像名（去除架构后缀）
        logger.info("步骤2: 重新tag为清理后的镜像名")
        tag_cmd1 = ["docker", "tag", ali_image, cleaned_image]
        if not self.run_command(tag_cmd1):
            logger.error(f"重新tag失败: {ali_image} -> {cleaned_image}")
            return False
        
        # 4. 重新tag为目标仓库镜像名
        target_image = f"{self.target_registry}/{cleaned_image}"
        logger.info("步骤3: 重新tag为目标仓库镜像名")
        tag_cmd2 = ["docker", "tag", cleaned_image, target_image]
        if not self.run_command(tag_cmd2):
            logger.error(f"重新tag失败: {cleaned_image} -> {target_image}")
            return False
        
        # 5. 推送到目标仓库
        logger.info("步骤4: 推送到目标仓库")
        push_cmd = ["docker", "push", target_image]
        if not self.run_command(push_cmd):
            logger.error(f"推送镜像失败: {target_image}")
            return False
        
        logger.info(f"镜像同步完成: {original_image} -> {target_image}")
        if was_modified:
            logger.info(f"注意: 已自动去除架构后缀")
        return True
    
    def cleanup_image(self, original_image: str) -> None:
        """
        清理本地镜像
        
        Args:
            original_image: 原始镜像名
        """
        cleaned_image, _ = self.process_image_tag(original_image)
        ali_image = self.convert_image_name(original_image)
        target_image = f"{self.target_registry}/{cleaned_image}"
        
        images_to_remove = [ali_image, cleaned_image, target_image]
        
        for image in images_to_remove:
            logger.info(f"清理本地镜像: {image}")
            rmi_cmd = ["docker", "rmi", image]
            self.run_command(rmi_cmd)
    
    def batch_sync(self, image_list: List[str], cleanup: bool = False) -> bool:
        """
        批量同步镜像
        
        Args:
            image_list: 镜像列表
            cleanup: 是否清理本地镜像
            
        Returns:
            bool: 是否全部同步成功
        """
        success_count = 0
        total_count = len(image_list)
        
        for image in image_list:
            logger.info(f"开始处理镜像 ({success_count + 1}/{total_count}): {image}")
            
            if self.sync_image(image):
                success_count += 1
                if cleanup:
                    self.cleanup_image(image)
            else:
                logger.error(f"镜像同步失败: {image}")
            
            logger.info("-" * 50)
        
        logger.info(f"同步完成: 成功 {success_count}/{total_count}")
        return success_count == total_count

def main():
    parser = argparse.ArgumentParser(description='Docker镜像同步工具')
    parser.add_argument('--target-registry', required=True, 
                       help='目标私有仓库地址，如 10.28.1.194:5000')
    parser.add_argument('--images', nargs='+', required=False,
                       help='要同步的镜像列表，如 registry.k8s.io/coredns/coredns:1.0')
    parser.add_argument('--image-file', 
                       help='包含镜像列表的文件，每行一个镜像')
    parser.add_argument('--cleanup', action='store_true',
                       help='同步完成后清理本地镜像')
    parser.add_argument('--debug', action='store_true',
                       help='启用调试模式')
    parser.add_argument('--keep-arch-suffix', action='store_true',
                       help='保留架构后缀 (默认会去除 -amd64 和 -arm64 后缀)')
    
    args = parser.parse_args()
    
    # 设置日志级别
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # 获取镜像列表
    images = args.images or []
    if args.image_file:
        try:
            with open(args.image_file, 'r', encoding='utf-8') as f:
                file_images = [line.strip() for line in f if line.strip()]
            images.extend(file_images)
        except FileNotFoundError:
            logger.error(f"文件不存在: {args.image_file}")
            sys.exit(1)
    
    if not images:
        logger.error("未指定要同步的镜像")
        sys.exit(1)
    
    logger.info(f"目标仓库: {args.target_registry}")
    logger.info(f"去除架构后缀: {not args.keep_arch_suffix}")
    logger.info(f"要同步的镜像数量: {len(images)}")
    logger.info(f"镜像列表: {images}")
    
    # 创建同步器并执行同步
    sync = DockerImageSync(args.target_registry, remove_arch_suffix=not args.keep_arch_suffix)
    success = sync.batch_sync(images, args.cleanup)
    
    if success:
        logger.info("所有镜像同步成功!")
        sys.exit(0)
    else:
        logger.error("部分镜像同步失败!")
        sys.exit(1)

if __name__ == "__main__":
    main()