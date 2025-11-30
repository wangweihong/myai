import importlib
import platform
import sys

def check_packages(packages):
    print("Python version:", sys.version)
    print("OS:", platform.platform())
    print("--------------------------------------------------")
    results = {}

    for pkg in packages:
        try:
            module = importlib.import_module(pkg)
            version = getattr(module, "__version__", None)
            if version is None:  # 有些库用 version / VERSION
                version = getattr(module, "version", None)
            results[pkg] = version if version else "Installed (version unknown)"
        except Exception as e:
            results[pkg] = f"Not installed ({str(e)})"

    return results


if __name__ == "__main__":
    ai_packages = [
        # ===== 深度学习基础库 =====
        "torch",
        "torchvision",
        "torchaudio",
        "tensorflow",
        "keras",
        "jax",

        # ===== CUDA / 加速相关 =====
        "triton",
        "flash_attn",
        "xformers",
        "onnx",
        "onnxruntime",
        "tensorrt",

        # ===== 数据处理 =====
        "numpy",
        "pandas",

        # ===== NLP / Transformers =====
        "transformers",
        "tokenizers",
        "accelerate",
        "sentencepiece",

        # ===== 科学计算 =====
        "scipy",
        "numba",
        "cupy",

        # ===== 可视化 =====
        "matplotlib",
        "seaborn",

        # ===== 其他常见模型/库 =====
        "diffusers",
        "gradio",
        "langchain",
        "pydantic",
    ]

    results = check_packages(ai_packages)

    print("AI package status:")
    for pkg, status in results.items():
        print(f"{pkg:15} : {status}")
