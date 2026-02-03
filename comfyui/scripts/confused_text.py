import argparse
import base64
import json
import os
import random
import shutil
import string
import zlib

import numpy as np


def generate_permutation_key(length, seed):
    """生成置换密钥"""
    random.seed(seed)
    return random.sample(range(length), length)

def apply_character_substitution(text, seed):
    """应用字符替换混淆"""
    # 创建字符映射表
    chars = list(string.ascii_letters + string.digits + string.punctuation + " ")
    shuffled_chars = chars.copy()
    random.seed(seed)
    random.shuffle(shuffled_chars)
    
    # 创建替换映射
    char_map = dict(zip(chars, shuffled_chars))
    
    # 应用替换
    confused_text = ''.join(char_map.get(c, c) for c in text)
    return confused_text, char_map

def reverse_character_substitution(text, char_map):
    """反转字符替换"""
    reverse_map = {v: k for k, v in char_map.items()}
    return ''.join(reverse_map.get(c, c) for c in text)

def apply_position_permutation(text, seed):
    """应用位置置换混淆"""
    # 转换为字符列表
    char_list = list(text)
    
    # 生成置换密钥
    permutation_key = generate_permutation_key(len(char_list), seed)
    
    # 应用置换
    permuted_chars = [char_list[i] for i in permutation_key]
    return ''.join(permuted_chars), permutation_key

def reverse_position_permutation(text, permutation_key):
    """反转位置置换"""
    char_list = list(text)
    restored_chars = [''] * len(char_list)
    
    for new_pos, orig_pos in enumerate(permutation_key):
        restored_chars[orig_pos] = char_list[new_pos]
    
    return ''.join(restored_chars)

def apply_compression(text):
    """应用压缩混淆"""
    compressed = zlib.compress(text.encode())
    return base64.b64encode(compressed).decode()

def reverse_compression(compressed_text):
    """反转压缩混淆"""
    decoded = base64.b64decode(compressed_text)
    return zlib.decompress(decoded).decode()

def apply_confusion(text, key, mode='basic'):
    """
    应用文字混淆
    :param text: 原始文本
    :param key: 混淆密钥
    :param mode: 混淆模式 ('basic', 'advanced')
    :return: 混淆后的文本和元数据
    """
    if mode == 'basic':
        # 基本混淆：位置置换
        confused_text, permutation_key = apply_position_permutation(text, key)
        return confused_text, {'permutation_key': permutation_key}
    
    elif mode == 'advanced':
        # 高级混淆：字符替换 + 位置置换 + 压缩
        # 字符替换
        substituted_text, char_map = apply_character_substitution(text, key)
        # 位置置换
        permuted_text, permutation_key = apply_position_permutation(substituted_text, key + 1)
        # 压缩
        compressed_text = apply_compression(permuted_text)
        
        return compressed_text, {
            'char_map': char_map,
            'permutation_key': permutation_key
        }
    
    else:
        raise ValueError(f"未知的混淆模式: {mode}")

def restore_text(confused_text, key, metadata, mode='basic'):
    """
    还原混淆文本
    :param confused_text: 混淆后的文本
    :param key: 混淆密钥
    :param metadata: 混淆元数据
    :param mode: 混淆模式
    :return: 还原后的原始文本
    """
    if mode == 'basic':
        # 反转位置置换
        return reverse_position_permutation(confused_text, metadata['permutation_key'])
    
    elif mode == 'advanced':
        # 解压缩
        decompressed_text = reverse_compression(confused_text)
        # 反转位置置换
        restored_permuted = reverse_position_permutation(decompressed_text, metadata['permutation_key'])
        # 反转字符替换
        return reverse_character_substitution(restored_permuted, metadata['char_map'])
    
    else:
        raise ValueError(f"未知的还原模式: {mode}")

def process_file(input_path, output_path, key, prefix, mode='confuse', text_mode='basic'):
    """
    处理单个文件
    :param input_path: 输入文件路径
    :param output_path: 输出文件路径
    :param key: 密钥
    :param prefix: 输出文件前缀
    :param mode: 处理模式 ('confuse' 或 'restore')
    :param text_mode: 文本混淆模式 ('basic', 'advanced')
    """
    try:
        # 确保输出目录存在
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        # 读取文件内容
        with open(input_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if mode == 'confuse':
            # 混淆处理
            confused_content, metadata = apply_confusion(content, key, text_mode)
            
            # 保存混淆后的内容
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(confused_content)
            
            # 保存元数据到单独文件
            metadata_path = f"{os.path.splitext(output_path)[0]}_meta.json"
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f)
            
            print(f"混淆成功: {input_path} -> {output_path}")
            return True
            
        elif mode == 'restore':
            # 查找元数据文件
            metadata_path = f"{os.path.splitext(input_path)[0]}_meta.json"
            if not os.path.exists(metadata_path):
                print(f"找不到元数据文件: {metadata_path}")
                return False
            
            # 加载元数据
            with open(metadata_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            
            # 还原处理
            restored_content = restore_text(content, key, metadata, text_mode)
            
            # 保存还原后的内容
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(restored_content)
            
            print(f"还原成功: {input_path} -> {output_path}")
            return True
    
    except Exception as e:
        print(f"处理失败: {input_path} - {str(e)}")
        return False

def process_directory(input_dir, output_dir, key, prefix, mode='confuse', text_mode='basic', recursive=True):
    """
    处理目录下的所有文本文件
    :param input_dir: 输入目录
    :param output_dir: 输出目录
    :param key: 密钥
    :param prefix: 输出文件前缀
    :param mode: 处理模式 ('confuse' 或 'restore')
    :param text_mode: 文本混淆模式 ('basic', 'advanced')
    :param recursive: 是否递归处理子目录
    """
    # 支持的文本文件格式
   # supported_formats = ['.txt', '.csv', '.json', '.xml', '.html', '.md', '.js', '.py']
    supported_formats = ['.txt', '.csv', '.xml', '.html', '.md', '.js', '.py']
    
    processed_count = 0
    skipped_count = 0
    
    # 遍历目录
    for root, _, files in os.walk(input_dir):
        # 如果不递归处理子目录，只处理顶层目录
        if not recursive and root != input_dir:
            continue
            
        # 计算输出目录中的相对路径
        rel_path = os.path.relpath(root, input_dir)
        output_path = os.path.join(output_dir, rel_path)
        
        # 创建输出目录
        os.makedirs(output_path, exist_ok=True)
        
        for file in files:
            # 检查文件扩展名
            if any(file.lower().endswith(ext) for ext in supported_formats):
                input_file = os.path.join(root, file)
                output_file = os.path.join(output_path, f"{prefix}{file}")
                
                if process_file(input_file, output_file, key, prefix, mode, text_mode):
                    processed_count += 1
                else:
                    skipped_count += 1
                    
        # 复制非文本文件（可选）
        # 如果需要保留非文本文件，可以添加以下代码
        # for file in files:
        #     if not any(file.lower().endswith(ext) for ext in supported_formats):
        #         input_file = os.path.join(root, file)
        #         output_file = os.path.join(output_path, file)
        #         shutil.copy2(input_file, output_file)
    
    print(f"\n处理完成！共处理 {processed_count} 个文件，跳过 {skipped_count} 个文件")

def main():
    # 命令行参数解析
    parser = argparse.ArgumentParser(description='文字混淆与还原工具 - 保持目录结构')
    parser.add_argument('input', help='输入文件或目录路径')
    parser.add_argument('key', help='混淆密钥 (字符串)')
    parser.add_argument('--mode', choices=['confuse', 'restore'], default='confuse', 
                        help='处理模式: confuse(混淆)或restore(还原)')
    parser.add_argument('--output', default='output', help='输出目录路径 (默认为"output")')
    parser.add_argument('--prefix', default='', help='输出文件前缀 (默认为空)')
    parser.add_argument('--text-mode', choices=['basic', 'advanced'], default='basic', 
                        help='文本混淆模式: basic(基本), advanced(高级)')
    parser.add_argument('--no-recursive', action='store_true', 
                        help='不递归处理子目录')
    parser.add_argument('--copy-non-text', action='store_true', 
                        help='复制非文本文件到输出目录')
    
    args = parser.parse_args()
    
    # 创建输出目录
    os.makedirs(args.output, exist_ok=True)
    
    # 检查输入路径是文件还是目录
    if os.path.isfile(args.input):
        # 处理单个文件
        input_file = args.input
        output_file = os.path.join(args.output, f"{prefix}{os.path.basename(args.input)}")
        
        process_file(
            input_path=input_file,
            output_path=output_file,
            key=args.key,
            prefix=args.prefix,
            mode=args.mode,
            text_mode=args.text_mode
        )
    elif os.path.isdir(args.input):
        # 处理目录
        process_directory(
            input_dir=args.input,
            output_dir=args.output,
            key=args.key,
            prefix=args.prefix,
            mode=args.mode,
            text_mode=args.text_mode,
            recursive=not args.no_recursive
        )
    else:
        print(f"错误: 路径 '{args.input}' 不存在或不是文件/目录")

if __name__ == "__main__":
    main()
