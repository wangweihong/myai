# base
comfyui基础构建相关
* image
  * [`@ComfyUI-Docker`](https://github.com/YanWenKun/ComfyUI-Docker/blob/main/README.zh.adoc) comfyui Docker image镜像构建仓库，支持`slim`和`megapak`
    * `slim`镜像仅预装基本的 ComfyUI 与 Manager，同时预装大量依赖项，方便后续无痛安装热门自定义节点（扩展插件）
    * `megapak`镜像为整合包，包含开发套件与常用自定义节点（扩展插件）
      * [内置节点列表](https://github.com/YanWenKun/ComfyUI-Docker/blob/main/cu128-megapak-pt29/builder-scripts/preload-cache.sh )
  
## 运行
```
mkdir -p \
  storage \ 
  storage-models/models \
  storage-models/hf-hub \
  storage-models/torch-hub \
  storage-user/input \
  storage-user/output \
  storage-user/workflows

docker run -it --rm \
  --name comfyui-megapak \
  --runtime nvidia \
  --gpus all \
  -p 8188:8188 \
  -v "$(pwd)"/storage:/root \
  -v "$(pwd)"/storage-models/models:/root/ComfyUI/models \
  -v "$(pwd)"/storage-models/hf-hub:/root/.cache/huggingface/hub \
  -v "$(pwd)"/storage-models/torch-hub:/root/.cache/torch/hub \
  -v "$(pwd)"/storage-user/input:/root/ComfyUI/input \
  -v "$(pwd)"/storage-user/output:/root/ComfyUI/output \
  -v "$(pwd)"/storage-user/workflows:/root/ComfyUI/user/default/workflows \
  -e CLI_ARGS="" \
  yanwk/comfyui-boot:cu128-megapak-pt29
```