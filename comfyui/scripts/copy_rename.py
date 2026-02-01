import os
import argparse
import shutil
import re

def get_max_sequence(dest_dir, prefix, digits=5):
    """
    获取目标目录中指定前缀文件的最大序号
    :param dest_dir: 目标目录
    :param prefix: 文件名前缀
    :param digits: 数字位数
    :return: 最大序号（如果找不到则返回0）
    """
    max_seq = 0
    pattern = re.compile(rf'^{re.escape(prefix)}(\d{{{digits}}})\.\w+$')
    
    for filename in os.listdir(dest_dir):
        match = pattern.match(filename)
        if match:
            seq = int(match.group(1))
            if seq > max_seq:
                max_seq = seq
    return max_seq

def copy_and_rename_files(src_dir, dest_dir, prefix, digits=5):
    """
    复制源目录文件到目标目录并按规则重命名
    :param src_dir: 源目录
    :param dest_dir: 目标目录
    :param prefix: 文件名前缀
    :param digits: 数字位数
    """
    # 确保目标目录存在
    os.makedirs(dest_dir, exist_ok=True)
    
    # 获取目标目录中最大序号
    start_seq = get_max_sequence(dest_dir, prefix, digits) + 1
    
    # 获取源目录所有文件（排除目录）
    src_files = [f for f in os.listdir(src_dir) 
                if os.path.isfile(os.path.join(src_dir, f))]
    
    # 按文件名排序（自然排序）
    src_files.sort(key=lambda f: [int(s) if s.isdigit() else s.lower() for s in re.split(r'(\d+)', f)])
    
    processed_count = 0
    
    for i, file in enumerate(src_files):
        # 获取文件扩展名
        _, ext = os.path.splitext(file)
        
        # 生成新文件名
        new_filename = f"{prefix}{start_seq + i:0{digits}d}{ext}"
        dest_path = os.path.join(dest_dir, new_filename)
        
        # 复制文件
        src_path = os.path.join(src_dir, file)
        shutil.copy2(src_path, dest_path)
        
        print(f"复制并重命名: {file} -> {new_filename}")
        processed_count += 1
    
    print(f"\n操作完成！共处理 {processed_count} 个文件")
    print(f"目标目录中 {prefix} 文件的最大序号现在为: {start_seq + processed_count - 1}")

def main():
    # 命令行参数解析
    parser = argparse.ArgumentParser(description='文件复制重命名工具')
    parser.add_argument('src_dir', help='源目录路径')
    parser.add_argument('dest_dir', help='目标目录路径')
    parser.add_argument('prefix', help='文件名前缀')
    parser.add_argument('--digits', type=int, default=5, 
                        help='数字位数 (默认为5位)')
    
    args = parser.parse_args()
    
    # 检查源目录是否存在
    if not os.path.isdir(args.src_dir):
        print(f"错误: 源目录 '{args.src_dir}' 不存在")
        return
    
    # 执行复制重命名操作
    copy_and_rename_files(
        src_dir=args.src_dir,
        dest_dir=args.dest_dir,
        prefix=args.prefix,
        digits=args.digits
    )

if __name__ == "__main__":
    main()
