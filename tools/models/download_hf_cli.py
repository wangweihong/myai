#!/usr/bin/env python3
"""
Hugging Face模型下载命令生成脚本
生成huggingface-cli下载命令，但不执行
支持huggingface.co和hf-mirrors.com域名

使用示例
python3 ./download_hf_cli.py https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn_scaled.safetensors ~/ai/ComfyUI/models/text_encoders/text2image/FLUX1/
解析成功:
  仓库ID: comfyanonymous/flux_text_encoders
  文件名: t5xxl_fp8_e4m3fn_scaled.safetensors
  目标目录: /home/wwhvw/ai/ComfyUI/models/text_encoders/text2image/FLUX1/

生成的下载命令:
--------------------------------------------------
hf download comfyanonymous/flux_text_encoders --include t5xxl_fp8_e4m3fn_scaled.safetensors --local-dir ~/ai/ComfyUI/models/text_encoders/text2image/FLUX1/
--------------------------------------------------

"""

import argparse
import os
import sys
from urllib.parse import urlparse

def parse_hf_url(url):
    """
    解析Hugging Face URL，提取仓库ID和文件名
    支持huggingface.co和hf-mirrors.com域名
    """
    try:
        parsed = urlparse(url)
        path_parts = parsed.path.split('/')
        
        # 检查是否支持该域名
        supported_domains = ['huggingface.co', 'hf-mirrors.com']
        if parsed.netloc not in supported_domains:
            raise ValueError(f"不支持的域名: {parsed.netloc}，支持的域名: {', '.join(supported_domains)}")
        
        # 查找 'resolve' 在路径中的位置
        if 'resolve' in path_parts:
            resolve_index = path_parts.index('resolve')
            repo_parts = path_parts[1:resolve_index]  # 跳过第一个空字符串
            file_parts = path_parts[resolve_index+2:]  # 跳过 'resolve' 和分支名
            
            repo_id = '/'.join(repo_parts)
            filename = '/'.join(file_parts)
            
            return repo_id, filename
        else:
            raise ValueError("URL格式不正确，未找到'resolve'路径段")
    except Exception as e:
        raise ValueError(f"解析URL失败: {e}")

def generate_hf_command(repo_id, filename, local_dir):
    """
    生成huggingface-cli下载命令
    """
    # 构建下载命令
    cmd_parts = [
        'HF_ENDPOINT=https://hf-mirror.com',
        'hf', 'download',
        repo_id,
        '--include',
        filename,
        '--local-dir', local_dir
    ]
    
    # 将命令各部分连接成一个字符串
    cmd = ' '.join(cmd_parts)
    return cmd

def main():
    parser = argparse.ArgumentParser(description='生成Hugging Face模型下载命令')
    parser.add_argument('url', help='Hugging Face文件URL (支持huggingface.co和hf-mirrors.com)')
    parser.add_argument('local_dir', help='本地存储目录')
    
    args = parser.parse_args()
    
    # 检查参数
    supported_domains = ['huggingface.co', 'hf-mirrors.com']
    parsed_url = urlparse(args.url)
    if parsed_url.netloc not in supported_domains:
        print(f"错误: 不支持的域名 {parsed_url.netloc}，支持的域名: {', '.join(supported_domains)}")
        sys.exit(1)
    
    if not os.path.exists(args.local_dir):
        try:
            os.makedirs(args.local_dir, exist_ok=True)
        except Exception as e:
            print(f"错误: 无法创建目录 {args.local_dir}: {e}")
            sys.exit(1)
    
    try:
        # 解析URL
        repo_id, filename = parse_hf_url(args.url)
        print(f"解析成功:")
        print(f"  仓库ID: {repo_id}")
        print(f"  文件名: {filename}")
        print(f"  目标目录: {args.local_dir}")
        
        # 生成命令
        cmd = generate_hf_command(repo_id, filename, args.local_dir)
        
        print("\n生成的下载命令:")
        print("-" * 50)
        print(cmd)
        print("-" * 50)
        
        print("\n提示: 您可以复制此命令并手动执行")
            
    except Exception as e:
        print(f"错误: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
