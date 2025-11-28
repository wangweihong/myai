#!/usr/bin/env python3
import os
import sys
import argparse
import subprocess
import time
from pathlib import Path
from typing import Optional, Dict, Any
from huggingface_hub import snapshot_download, HfApi
from modelscope import snapshot_download as ms_snapshot_download
import git
from tqdm import tqdm
from rich.console import Console
from rich.progress import (
    Progress, 
    SpinnerColumn, 
    TextColumn, 
    BarColumn, 
    TaskProgressColumn,
    TimeRemainingColumn,
    DownloadColumn,
    TransferSpeedColumn
)

console = Console()

class ProgressTracker:
    """进度跟踪器"""
    
    def __init__(self):
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            "•",
            DownloadColumn(),
            "•",
            TransferSpeedColumn(),
            "•",
            TimeRemainingColumn(),
            console=console
        )
        self.tasks = {}
    
    def start_task(self, task_id: str, description: str, total: int = 100):
        """开始新任务"""
        self.tasks[task_id] = self.progress.add_task(description, total=total)
    
    def update_task(self, task_id: str, advance: int = 1):
        """更新任务进度"""
        if task_id in self.tasks:
            self.progress.update(self.tasks[task_id], advance=advance)
    
    def complete_task(self, task_id: str):
        """完成任务"""
        if task_id in self.tasks:
            self.progress.update(self.tasks[task_id], completed=100)
            self.progress.stop_task(self.tasks[task_id])
    
    def __enter__(self):
        self.progress.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.progress.stop()

class ModelDownloader:
    def __init__(self):
        self.supported_methods = ['hf', 'modelscope', 'git']
        self.progress_tracker = ProgressTracker()
    
    def parse_arguments(self):
        parser = argparse.ArgumentParser(
            description='智能模型下载器 - 支持 HuggingFace、ModelScope 和 Git LFS',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
使用示例:
  # 使用默认方法 (HF) 下载模型
  python model_downloader.py --model microsoft/DialoGPT-medium
  
  # 指定下载方法和输出目录
  python model_downloader.py --method modelscope --model damo/nlp_structbert_backbone_base_std --output ./models
  
  # 使用 Git LFS 下载并指定分支
  python model_downloader.py --method git --model https://huggingface.co/microsoft/DialoGPT-medium --revision main
  
  # 下载私有模型 (需要 token)
  python model_downloader.py --model your-private-model --token hf_xxxxxxxxxx
            """
        )
        
        parser.add_argument('--method', '-m', 
                          choices=self.supported_methods, 
                          default='hf',
                          help='下载方法: hf (HuggingFace), modelscope, git (默认: hf)')
        parser.add_argument('--model', '-n', required=True, 
                          help='模型名称或仓库地址')
        parser.add_argument('--output', '-o', default='./models',
                          help='输出目录路径 (默认: ./models)')
        parser.add_argument('--revision', '-r', default='main',
                          help='模型版本/分支 (默认: main)')
        parser.add_argument('--token', '-t', 
                          help='访问令牌 (用于私有模型)')
        parser.add_argument('--cache-dir',
                          help='缓存目录路径')
        parser.add_argument('--quiet', '-q', action='store_true',
                          help='安静模式，不显示进度条')
        
        return parser.parse_args()
    
    def detect_download_method(self, model_identifier: str) -> str:
        """自动检测下载方法"""
        if model_identifier.startswith(('http://', 'https://')):
            return 'git'
        elif '/' in model_identifier and len(model_identifier.split('/')) == 2:
            # 尝试 HF 和 ModelScope 格式
            return 'hf'
        else:
            # ModelScope 格式的模型ID
            return 'modelscope'
    
    def hf_progress_callback(self, progress_info: Dict[str, Any]):
        """HF下载进度回调"""
        if progress_info.get('status') == 'downloading':
            description = f"下载 {progress_info.get('filename', '文件')}"
            if 'downloaded' in progress_info and 'total' in progress_info:
                downloaded = progress_info['downloaded']
                total = progress_info['total']
                if total > 0:
                    percentage = (downloaded / total) * 100
                    console.print(f"{description}: {downloaded}/{total} bytes ({percentage:.1f}%)")
    
    def download_via_hf(self, model_name: str, output_dir: str, revision: str, 
                       token: Optional[str], cache_dir: Optional[str], quiet: bool = False) -> bool:
        """通过 HuggingFace Hub 下载模型"""
        
        with self.progress_tracker:
            self.progress_tracker.start_task("hf_download", f"下载 HF 模型: {model_name}")
            
            console.print(f"[bold blue]🚀 通过 HuggingFace Hub 下载模型: {model_name}[/bold blue]")
            
            download_kwargs = {
                'repo_id': model_name,
                'local_dir': output_dir,
                'revision': revision,
                'local_dir_use_symlinks': False,
                'resume_download': True,
            }
            
            if not quiet:
                download_kwargs['progress_callback'] = self.hf_progress_callback
            
            if token:
                download_kwargs['token'] = token
                console.print("🔑 使用提供的 token 进行身份验证")
            
            if cache_dir:
                download_kwargs['cache_dir'] = cache_dir
            
            try:
                start_time = time.time()
                snapshot_download(**download_kwargs)
                end_time = time.time()
                
                self.progress_tracker.complete_task("hf_download")
                console.print(f"[bold green]✅ 模型已成功下载到: {output_dir}[/bold green]")
                console.print(f"⏱️  下载耗时: {end_time - start_time:.2f} 秒")
                return True
                
            except Exception as e:
                console.print(f"[bold red]❌ HuggingFace 下载失败: {e}[/bold red]")
                return False
    
    def download_via_modelscope(self, model_name: str, output_dir: str, revision: str, 
                              cache_dir: Optional[str], quiet: bool = False) -> bool:
        """通过 ModelScope 下载模型"""
        
        with self.progress_tracker:
            self.progress_tracker.start_task("ms_download", f"下载 ModelScope 模型: {model_name}")
            
            console.print(f"[bold blue]🚀 通过 ModelScope 下载模型: {model_name}[/bold blue]")
            
            download_kwargs = {
                'model_id': model_name,
                'cache_dir': output_dir,
                'revision': revision,
            }
            
            if cache_dir:
                download_kwargs['cache_dir'] = cache_dir
            
            try:
                start_time = time.time()
                model_path = ms_snapshot_download(**download_kwargs)
                end_time = time.time()
                
                self.progress_tracker.complete_task("ms_download")
                console.print(f"[bold green]✅ 模型已成功下载到: {model_path}[/bold green]")
                console.print(f"⏱️  下载耗时: {end_time - start_time:.2f} 秒")
                return True
                
            except Exception as e:
                console.print(f"[bold red]❌ ModelScope 下载失败: {e}[/bold red]")
                return False
    
    def download_via_git(self, repo_url: str, output_dir: str, revision: str, 
                        token: Optional[str], quiet: bool = False) -> bool:
        """通过 Git LFS 下载模型"""
        
        with self.progress_tracker:
            self.progress_tracker.start_task("git_download", f"Git 克隆: {repo_url}")
            
            console.print(f"[bold blue]🚀 通过 Git LFS 下载模型: {repo_url}[/bold blue]")
            
            # 处理认证信息
            if token and 'huggingface.co' in repo_url:
                if not repo_url.startswith('https://'):
                    repo_url = f"https://huggingface.co/{repo_url}"
                auth_repo_url = repo_url.replace(
                    'https://', 
                    f'https://user:{token}@'
                )
                console.print("🔑 使用 token 进行 Git 认证")
            else:
                auth_repo_url = repo_url
            
            try:
                # 创建输出目录
                Path(output_dir).mkdir(parents=True, exist_ok=True)
                
                # 克隆仓库
                start_time = time.time()
                console.print(f"📥 克隆仓库到: {output_dir}")
                
                if quiet:
                    # 安静模式
                    repo = git.Repo.clone_from(auth_repo_url, output_dir, branch=revision, depth=1)
                else:
                    # 显示进度
                    repo = git.Repo.clone_from(auth_repo_url, output_dir, branch=revision)
                
                self.progress_tracker.complete_task("git_download")
                self.progress_tracker.start_task("git_lfs", "拉取 LFS 文件")
                
                # 拉取 LFS 文件
                console.print("📦 拉取 Git LFS 文件...")
                result = subprocess.run(
                    ['git', 'lfs', 'pull'], 
                    cwd=output_dir, 
                    capture_output=not quiet,
                    text=True
                )
                
                if result.returncode != 0:
                    console.print(f"[yellow]⚠️  Git LFS 拉取警告: {result.stderr}[/yellow]")
                
                end_time = time.time()
                self.progress_tracker.complete_task("git_lfs")
                
                console.print(f"[bold green]✅ Git 仓库已成功克隆到: {output_dir}[/bold green]")
                console.print(f"⏱️  下载耗时: {end_time - start_time:.2f} 秒")
                return True
                
            except Exception as e:
                console.print(f"[bold red]❌ Git 下载失败: {e}[/bold red]")
                return False
    
    def run(self):
        args = self.parse_arguments()
        
        # 如果未指定方法，自动检测
        actual_method = args.method
        if actual_method == 'hf':
            # 进一步检测是否是 ModelScope 格式
            if not args.model.startswith(('http://', 'https://')) and args.model.count('/') != 1:
                actual_method = 'modelscope'
                console.print(f"[yellow]🔍 检测到 ModelScope 格式模型，自动切换到 modelscope 下载[/yellow]")
        
        # 创建输出目录
        Path(args.output).mkdir(parents=True, exist_ok=True)
        
        console.print(f"[bold]🎯 下载配置:[/bold]")
        console.print(f"  方法: {actual_method}")
        console.print(f"  模型: {args.model}")
        console.print(f"  输出: {args.output}")
        console.print(f"  版本: {args.revision}")
        if args.token:
            console.print(f"  认证: 使用提供的 token")
        
        success = False
        
        if actual_method == 'hf':
            success = self.download_via_hf(
                args.model, 
                args.output, 
                args.revision, 
                args.token,
                args.cache_dir,
                args.quiet
            )
        elif actual_method == 'modelscope':
            success = self.download_via_modelscope(
                args.model, 
                args.output, 
                args.revision,
                args.cache_dir,
                args.quiet
            )
        elif actual_method == 'git':
            success = self.download_via_git(
                args.model, 
                args.output, 
                args.revision, 
                args.token,
                args.quiet
            )
        
        if success:
            console.print(f"[bold green]🎉 模型下载完成![/bold green]")
            
            # 显示下载的文件信息
            model_dir = Path(args.output)
            if model_dir.exists():
                total_size = sum(f.stat().st_size for f in model_dir.rglob('*') if f.is_file())
                file_count = sum(1 for _ in model_dir.rglob('*') if _.is_file())
                console.print(f"📊 文件统计: {file_count} 个文件, 总大小: {total_size / (1024**3):.2f} GB")
        else:
            console.print(f"[bold red]💥 下载失败，请检查参数和网络连接[/bold red]")
            sys.exit(1)

def main():
    downloader = ModelDownloader()
    downloader.run()

if __name__ == '__main__':
    main()