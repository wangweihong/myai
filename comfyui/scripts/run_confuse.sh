#!/bin/bash
python ./confused_image.py ../assets/images/confuse 92xxxx --mode confuse --output ../assets/images/confused_images --prefix confused_
python ./confused_text.py ../prompts/confuse 92xxxx --mode confuse --output ../prompts/confused_files --prefix confused_