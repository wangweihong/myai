# 操作
## 图片
* 混淆
  * `python ./confused_image.py ../assets/images/confuse 123456 --mode confuse --output ../assets/images/confused_images --prefix confused_`
* 还原
  * ` python ./confused_image.py ../assets/images/confused_images 123456 --mode restore --output ../assets/images/restored_images`
  
## 文本
* 混淆
  * `python ./confused_text.py ../prompts/confuse 123456 --mode confuse --output ../prompts/confused_files --prefix confused_`
* 还原
  * `python ./confused_text.py ../prompts/confused_files 123456 --mode restore --output ../prompts/restored_files`
  

## 重命名

`python ./copy_rename.py ../assets/images/openpose ../assets/test/ openpose`
* 将其文件复制到另一个目录，并进行重命名。比如原文件为xxx.jpg, 可以指定前缀test，如果目标目录中存在该前缀test的文件且以5个数字结尾，如test00012.png，则该文件命名为test00013.jpg  
  * `../assets/openpose`: 原文件目录
  * `../assets/test`: 目标路径
  * `openpose`: 文件前缀