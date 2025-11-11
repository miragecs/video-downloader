# 使用Python官方镜像（更稳定，已包含pip）
# 构建时使用: docker build --platform linux/arm64 -t video-downloader .
# 或: docker build --platform linux/arm/v7 -t video-downloader .
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 配置apt使用国内镜像源（解决网络问题）
# 处理不同Debian版本的sources文件格式
RUN set -eux; \
    # 尝试替换现有sources文件中的镜像源
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list; \
        sed -i 's@http://deb.debian.org@http://mirrors.aliyun.com@g' /etc/apt/sources.list; \
        sed -i 's@https://deb.debian.org@http://mirrors.aliyun.com@g' /etc/apt/sources.list; \
    fi; \
    # 处理sources.list.d目录中的文件
    if [ -d /etc/apt/sources.list.d ]; then \
        find /etc/apt/sources.list.d -type f \( -name "*.list" -o -name "*.sources" \) -exec sed -i 's/deb.debian.org/mirrors.aliyun.com/g' {} \; 2>/dev/null || true; \
        find /etc/apt/sources.list.d -type f \( -name "*.list" -o -name "*.sources" \) -exec sed -i 's@http://deb.debian.org@http://mirrors.aliyun.com@g' {} \; 2>/dev/null || true; \
        find /etc/apt/sources.list.d -type f \( -name "*.list" -o -name "*.sources" \) -exec sed -i 's@https://deb.debian.org@http://mirrors.aliyun.com@g' {} \; 2>/dev/null || true; \
    fi; \
    # 如果替换失败，创建新的sources.list（需要检测Debian版本）
    if ! grep -q "mirrors.aliyun.com" /etc/apt/sources.list 2>/dev/null && \
       ! find /etc/apt/sources.list.d -type f -exec grep -q "mirrors.aliyun.com" {} \; 2>/dev/null; then \
        DEBIAN_VERSION=$(cat /etc/debian_version 2>/dev/null | cut -d. -f1 || echo "11"); \
        if [ "$DEBIAN_VERSION" = "12" ] || [ "$DEBIAN_VERSION" = "bookworm" ]; then \
            echo "deb http://mirrors.aliyun.com/debian/ bookworm main" > /etc/apt/sources.list && \
            echo "deb http://mirrors.aliyun.com/debian-security bookworm-security main" >> /etc/apt/sources.list && \
            echo "deb http://mirrors.aliyun.com/debian/ bookworm-updates main" >> /etc/apt/sources.list; \
        else \
            echo "deb http://mirrors.aliyun.com/debian/ bullseye main" > /etc/apt/sources.list && \
            echo "deb http://mirrors.aliyun.com/debian-security bullseye-security main" >> /etc/apt/sources.list && \
            echo "deb http://mirrors.aliyun.com/debian/ bullseye-updates main" >> /etc/apt/sources.list; \
        fi; \
    fi

# 安装必要的系统依赖（包括playwright需要的依赖）
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libwayland-client0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    libu2f-udev \
    libvulkan1 \
    && rm -rf /var/lib/apt/lists/* || \
    echo "Warning: Failed to install some dependencies, continuing..."

# 复制requirements文件
COPY requirements.txt .

# 安装Python依赖（使用国内镜像源加速）
# 优先使用清华镜像源，如果失败则使用默认源
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt || \
    pip install --no-cache-dir -i https://pypi.douban.com/simple -r requirements.txt || \
    pip install --no-cache-dir -r requirements.txt

# 安装playwright浏览器（可选，如果失败不影响运行）
RUN python -m playwright install chromium || \
    echo "Warning: Failed to install playwright browser, some features may not work"

# 复制应用文件
COPY app.py .
COPY templates/ templates/

# 创建必要的目录（使用绝对路径，确保在/app目录下）
RUN mkdir -p /app/templates /app/static /app/downloads /app/downloads/temp

# 设置下载目录权限（允许应用写入）
RUN chmod -R 777 /app/downloads || true

# 暴露端口
EXPOSE 5000

# 运行应用
CMD ["python", "app.py"]
