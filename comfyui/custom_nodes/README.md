为了备份节点列表，避免出现`SongBloom`跑路的情况

# 第三方节点
## 提示词
* `@ComfyUI-Prompt-Assistant`: 提示词小助手
* `ComfyUI-Cinematic-Prompt`: 带有预览功能的可视化界面构建复杂的电影提示
* `@ComfyUI-qwenmultiangle`: 生成Qwen多角度调试词

## 高斯3d
* `@comfyui-GaussianViewer`: 高斯泼溅PLY 文件的交互式 3D 预览和高质量图像
* `@comfyui-GeometryPack`: 3d模型相关节点 




# [如何管理](https://iphysresearch.github.io/blog/post/programing/git/git_submodule/9)
## 添加新节点
```
git submodule add https://github.com/yedp123/ComfyUI-Cinematic-Prompt.git @ComfyUI-Cinematic-Prompt
```

## 删除
1. 删除子模块文件夹
```
$ git rm --cached GWToolkit
$ rm -rf GWToolkit
```
2. 删除 `.gitmodules`文件中相关子模块的信息，类似于：
```
[submodule "GWToolkit"]
        path = GWToolkit
        url = https://github.com/iphysresearch/GWToolkit.git
```
3. 删除`.git/config`中相关子模块信息，类似于：
```
[submodule "GWToolkit"]
        url = https://github.com/iphysresearch/GWToolkit.git
        active = true
```
4 删除 .git 文件夹中的相关子模块文件
```
$ rm -rf .git/modules/GWToolkit
```