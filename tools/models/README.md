# 说明
* `download_hf_cli.py`: 将huggingface某个仓库的模型下载链接转换成`hf download`命令
  * ‵python3 ./download_hf_cli.py https://huggingface.co/comfyanonymous/flux_text_encoders/resolve/main/t5xxl_fp8_e4m3fn_scaled.safetensors ~/ai/ComfyUI/models/text_encoders/text2image/FLUX1/‵
    * 生成将‵t5xxl_fp8_e4m3fn_scaled.safetensors‵下载到本地目录的命令`HF_ENDPOINT=https://hf-mirror.com hf download comfyanonymous/flux_text_encoders --include t5xxl_fp8_e4m3fn_scaled.safetensors --local-dir ~/ai/ComfyUI/models/text_encoders/text2image/FLUX1/`