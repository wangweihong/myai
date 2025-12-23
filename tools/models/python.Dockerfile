# 使用 Ubuntu 22.04 作为基础镜像
FROM registry.cn-hangzhou.aliyuncs.com/eazycloud/ubuntu:24.04


# 设置环境变量
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_PYTHON_DOWNLOADS=https://cdn.npmmirror.com/binaries/python \
    PATH="/root/.local/bin:/root/.cargo/bin:${PATH}"

# 更新并安装基础工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl git build-essential ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# 安装独立 uv
#RUN curl -LsSf https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz -o uv-x86_64-unknown-linux-gnu.tar.gz \
#    && tar -xzf uv-x86_64-unknown-linux-gnu.tar.gz \
#    && chmod +x uv \
#    && mv uv /usr/local/bin/ \
#    && rm -f uv-x86_64-unknown-linux-gnu.tar.gz
#RUN curl -LsSf https://astral.sh/uv/install.sh | sh
COPY uv-x86_64-unknown-linux-gnu.tar.gz .
RUN tar -xzf uv-x86_64-unknown-linux-gnu.tar.gz \
    && chmod +x uv-x86_64-unknown-linux-gnu/uv \
    && chmod +x uv-x86_64-unknown-linux-gnu/uvx \
    && mv uv-x86_64-unknown-linux-gnu/uv /usr/local/bin/ \
    && mv uv-x86_64-unknown-linux-gnu/uvx /usr/local/bin/ \
    && rm -f uv-x86_64-unknown-linux-gnu.tar.gz \
    && rm -rf uv-x86_64-unknown-linux-gnu

# 配置 uv 使用清华源
ENV UV_PYTHON_DOWNLOADS=auto
# speed uv python install
ENV UV_PYTHON_INSTALL_MIRROR="https://ghfast.top/https://github.com/astral-sh/python-build-standalone/releases/download"
# 使用 uv 安装 Python 3.12
RUN uv python install 3.12

# 设置 Python 3.12 为默认 Python
RUN ln -sf $(uv python find 3.12) /usr/local/bin/python \
    && ln -sf $(uv python find 3.12) /usr/local/bin/python3

# 创建项目目录
WORKDIR /workspace


ENV UV_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple"
ENV UV_EXTRA_INDEX_URL="https://mirrors.aliyun.com/pypi/simple"
ENV HF_ENDPOINT="https://hf-mirror.com"

#RUN uv pip install torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cu130
RUN uv venv .venv && chmod +x .venv/bin/activate && .venv/bin/activate && \
#        uv pip install torch torchvision torchaudio --extra-index-url https://mirrors.tuna.tsinghua.edu.cn/pytorch-wheels/cu130 && \
        uv tool install modelscope && \
        uv tool install hf

RUN echo "source /workspace/.venv/bin/activate" >> ~/.bashrc
# 设置默认命令
CMD ["/bin/bash"]
