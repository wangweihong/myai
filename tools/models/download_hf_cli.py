#!/usr/bin/env python3
"""
Hugging Face模型下载命令生成脚本
生成huggingface-cli下载命令，但不执行
支持huggingface.co和hf-mirrors.com域名
支持包含'resolve'和'blob'路径的URL

使用示例
python3 ./download_hf_cli.py https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn_scaled.safetensors ~/ai/ComfyUI/models/text_encoders/text2image/FLUX1/
解析成功:
  仓库ID: comfyanonymous/flux_text_encoders
  文件名: t5xxl_fp8_e4m3fn_scaled.safetensors
  目标目录: /home/wwhvw/ai/ComfyUI/models/text_encoders/text2image/FLUX1/

生成的下载命令:
--------------------------------------------------
HF_ENDPOINT=https://hf-mirror.com hf download comfyanonymous/flux_text_encoders --include t5xxl_fp8_e4m3fn_scaled.safetensors --local-dir ~/ai/ComfyUI/models/text_encoders/text2image/FLUX1/
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
    支持包含'resolve'和'blob'路径的URL
    """
    try:
        parsed = urlparse(url)
        path_parts = parsed.path.split('/')

        # 检查是否支持该域名
        supported_domains = ['huggingface.co', 'hf-mirrors.com']
        if parsed.netloc not in supported_domains:
            raise ValueError(f"不支持的域名: {parsed.netloc}，支持的域名: {', '.join(supported_domains)}")

        # 查找 'resolve' 或 'blob' 在路径中的位置
        if 'resolve' in path_parts:
            index_key = 'resolve'
        elif 'blob' in path_parts:
            index_key = 'blob'
        else:
            raise ValueError("URL格式不正确，未找到'resolve'或'blob'路径段")

        # 找到路径中 'resolve' 或 'blob' 的位置
        index = path_parts.index(index_key)

        # 确保路径有足够的段
        if index >= len(path_parts) - 2:
            raise ValueError(f"URL格式不正确，{index_key}后缺少分支名和文件名")

        # 提取仓库ID（从第一个非空部分到'resolve'或'blob'之前）
        repo_id = '/'.join(path_parts[1:index])  # 跳过第一个空字符串

        # 提取文件名（跳过'resolve'或'blob'和分支名）
        filename = '/'.join(path_parts[index+2:])

        # 确保仓库ID和文件名都不为空
        if not repo_id:
            raise ValueError("无法从URL中提取仓库ID")
        if not filename:
            raise ValueError("无法从URL中提取文件名")

        return repo_id, filename
    except ValueError as e:
        raise ValueError(f"解析URL失败: {e}")
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
