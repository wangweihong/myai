import argparse
import os
import random
import numpy as np
from PIL import Image
import shutil

def apply_confusion(pixels, key):
    """
    应用高级混淆：像素置乱 + 通道分离 + XOR
    :param pixels: 原始像素数组
    :param key: 混淆密钥
    :return: 混淆后的像素数组
    """
    # 确保使用RGB模式
    if len(pixels.shape) == 2:
        pixels = np.stack([pixels]*3, axis=-1)
    
    # 创建副本避免修改原数组
    pixels = pixels.copy()
    
    # 第一步：像素置乱
    h, w, c = pixels.shape
    flattened = pixels.reshape(-1, c)
    np.random.seed(key)
    permutation = np.random.permutation(flattened.shape[0])
    shuffled = flattened[permutation].reshape(h, w, c)
    
    # 第二步：通道分离与重组
    r = shuffled[:, :, 0]
    g = shuffled[:, :, 1]
    b = shuffled[:, :, 2]
    
    # 使用密钥派生不同通道密钥
    r_key = (key + 37) % 256
    g_key = (key + 117) % 256
    b_key = (key + 231) % 256
    
    # 第三步：应用异或操作
    r_confused = r ^ r_key
    g_confused = g ^ g_key
    b_confused = b ^ b_key
    
    # 重组通道（改变通道顺序）
    confused_pixels = np.stack([g_confused, b_confused, r_confused], axis=-1)
    
    return confused_pixels, permutation

def restore_image(pixels, key, permutation):
    """
    还原混淆图像
    :param pixels: 混淆像素数组
    :param key: 混淆密钥
    :param permutation: 置乱顺序
    :return: 还原后的像素数组
    """
    # 分离通道
    g_confused = pixels[:, :, 0]
    b_confused = pixels[:, :, 1]
    r_confused = pixels[:, :, 2]
    
    # 派生通道密钥
    r_key = (key + 37) % 256
    g_key = (key + 117) % 256
    b_key = (key + 231) % 256
    
    # 应用异或还原
    r = r_confused ^ r_key
    g = g_confused ^ g_key
    b = b_confused ^ b_key
    
    # 重组通道
    restored_shuffled = np.stack([r, g, b], axis=-1)
    
    # 还原像素位置
    h, w, c = restored_shuffled.shape
    flattened = restored_shuffled.reshape(-1, c)
    
    # 创建逆置换索引
    inverse_permutation = np.argsort(permutation)
    restored_flat = flattened[inverse_permutation]
    
    return restored_flat.reshape(h, w, c)

def process_image(image_path, key, output_dir, prefix, mode='confuse'):
    """
    处理单个图片文件：混淆或还原
    :param image_path: 图片路径
    :param key: 密钥
    :param output_dir: 输出目录
    :param prefix: 输出文件前缀
    :param mode: 处理模式 ('confuse' 或 'restore')
    """
    try:
        # 创建输出目录
        os.makedirs(output_dir, exist_ok=True)
        
        # 获取文件名和扩展名
        filename = os.path.basename(image_path)
        name, ext = os.path.splitext(filename)
        
        # 构建输出路径（强制使用PNG格式）
        output_filename = f"{prefix}{name}.png"
        output_path = os.path.join(output_dir, output_filename)
        
        # 打开图片并转换为RGB模式
        img = Image.open(image_path).convert('RGB')
        pixels = np.array(img)
        
        if mode == 'confuse':
            # 混淆处理
            confused_pixels, permutation = apply_confusion(pixels, key)
            
            # 保存混淆后的图片和置乱数据
            confused_img = Image.fromarray(confused_pixels.astype('uint8'))
            confused_img.save(output_path)
            
            # 保存置乱数据到单独文件
            permutation_path = os.path.join(output_dir, f"{prefix}{name}_perm.npy")
            np.save(permutation_path, permutation)
            
            print(f"混淆成功: {image_path} -> {output_path}")
            return True
            
        elif mode == 'restore':
            # 查找对应的置乱数据文件
            perm_file = os.path.join(os.path.dirname(image_path), f"{prefix}{name}_perm.npy")
            if not os.path.exists(perm_file):
                print(f"找不到对应的置乱数据文件: {perm_file}")
                return False
                
            # 加载置乱数据
            permutation = np.load(perm_file)
            
            # 还原处理
            restored_pixels = restore_image(pixels, key, permutation)
            
            # 保存还原后的图片
            restored_img = Image.fromarray(restored_pixels.astype('uint8'))
            restored_img.save(output_path)
            
            print(f"还原成功: {image_path} -> {output_path}")
            return True
    
    except Exception as e:
        print(f"处理失败: {image_path} - {str(e)}")
        return False

def process_directory(input_dir, key, output_dir, prefix, mode='confuse', recursive=True):
    """
    处理目录下的所有图片文件
    :param input_dir: 输入目录
    :param key: 密钥
    :param output_dir: 输出目录
    :param prefix: 输出文件前缀
    :param mode: 处理模式 ('confuse' 或 'restore')
    :param recursive: 是否递归处理子目录
    """
    # 支持的图片格式
    supported_formats = ['.png', '.jpg', '.jpeg', '.bmp', '.tiff', '.webp']
    
    processed_count = 0
    skipped_count = 0
    
    # 遍历目录
    for root, _, files in os.walk(input_dir):
        # 如果不递归处理子目录，只处理顶层目录
        if not recursive and root != input_dir:
            continue
            
        # 计算相对路径
        rel_path = os.path.relpath(root, input_dir)
        output_subdir = os.path.join(output_dir, rel_path)
        
        # 创建输出子目录
        os.makedirs(output_subdir, exist_ok=True)
        
        for file in files:
            # 检查文件扩展名
            if any(file.lower().endswith(ext) for ext in supported_formats):
                file_path = os.path.join(root, file)
                if process_image(file_path, key, output_subdir, prefix, mode):
                    processed_count += 1
                else:
                    skipped_count += 1
    
    print(f"\n处理完成！共处理 {processed_count} 个文件，跳过 {skipped_count} 个文件")

def main():
    # 命令行参数解析
    parser = argparse.ArgumentParser(description='增强版图片混淆与还原工具（保留目录结构）')
    parser.add_argument('input', help='输入文件或目录路径')
    parser.add_argument('key', type=int, help='混淆密钥 (整数)')
    parser.add_argument('--mode', choices=['confuse', 'restore'], default='confuse', 
                        help='处理模式: confuse(混淆)或restore(还原)')
    parser.add_argument('--output', default='output', help='输出目录路径 (默认为"output")')
    parser.add_argument('--prefix', default='', help='输出文件前缀 (默认为空)')
    parser.add_argument('--no-recursive', action='store_true', 
                        help='不递归处理子目录')
    
    args = parser.parse_args()
    
    # 创建输出目录
    os.makedirs(args.output, exist_ok=True)
    
    # 检查输入路径是文件还是目录
    if os.path.isfile(args.input):
        # 处理单个文件
        input_file = args.input
        output_subdir = args.output  # 对于单个文件，输出到指定目录
        process_image(
            image_path=input_file,
            key=args.key,
            output_dir=output_subdir,
            prefix=args.prefix,
            mode=args.mode
        )
    elif os.path.isdir(args.input):
        # 处理目录
        process_directory(
            input_dir=args.input,
            key=args.key,
            output_dir=args.output,
            prefix=args.prefix,
            mode=args.mode,
            recursive=not args.no_recursive
        )
    else:
        print(f"错误: 路径 '{args.input}' 不存在或不是文件/目录")

if __name__ == "__main__":
    main()
