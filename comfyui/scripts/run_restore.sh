#!/bin/bash
 python ./confused_image.py ../assets/images/confused_images 92xxxx --mode restore --output ../assets/images/restored_images
 python ./confused_text.py ../prompts/confused_files 123456 --mode restore --output ../prompts/restored_files

 mv  ../assets/images/restored_images ../assets/images/confuse
 mv  ../prompts/restored_files ../prompts/confuse