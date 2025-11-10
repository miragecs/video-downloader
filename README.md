# M3U8视频下载工具

一个基于Flask的Web应用，可以解析网页中的M3U8链接并调用API进行下载。

## 功能特性

- 🌐 网页前端界面，简单易用
- 🔍 自动解析网页中的M3U8播放地址
- 📝 自动提取视频名称
- 🎯 支持多个M3U8链接选择
- 🚀 调用下载API进行视频下载

## 系统要求

- Debian ARM (ARM64/ARMv7)
- Docker 和 Docker Compose

## 快速开始

### 使用Docker Compose部署（推荐）

1. 确保所有文件在同一个目录下

2. 构建并启动容器：
```bash
docker-compose up -d --build
```

3. 访问应用：
打开浏览器访问 `http://your-server-ip:5000`

### 使用Docker命令部署

1. 构建镜像：
```bash
# 标准构建
docker build -t video-downloader .

# 如果网络有问题，可以指定平台（ARM架构）
docker build --platform linux/amd64 -t video-downloader .

# 如果使用代理
docker build --build-arg HTTP_PROXY=http://your-proxy:port -t video-downloader .
```

2. 运行容器：
```bash
docker run -d -p 5000:5000 --name video-downloader video-downloader
```

## 使用方法

1. 在网页输入框中输入包含视频的网页地址
2. 点击"解析M3U8链接"按钮
3. 如果有多个M3U8链接，选择要下载的视频
4. 填写下载设置（输出格式为必填项）
5. 点击"开始下载"提交任务

## API说明

### 解析M3U8链接

**端点**: `POST /api/parse`

**请求体**:
```json
{
  "url": "https://example.com/video-page"
}
```

**响应**:
```json
{
  "success": true,
  "m3u8_list": [
    {
      "url": "http://example.com/video.m3u8",
      "name": "视频名称"
    }
  ],
  "page_title": "页面标题"
}
```

### 提交下载任务

**端点**: `POST /api/download`

**请求体**:
```json
{
  "name": "视频名称",
  "url": "http://example.com/video.m3u8",
  "useragent": "",
  "dir": "test/12",
  "preset": "",
  "username": "",
  "password": "",
  "outputformat": "mp4"
}
```

## 配置说明

下载API地址默认配置为：`http://192.168.1.52:8081/down`

如需修改，请编辑 `app.py` 文件中的 `api_url` 变量。

## 停止服务

```bash
docker-compose down
```

或

```bash
docker stop video-downloader
docker rm video-downloader
```

## 故障排除

1. **Docker构建失败：无法解析deb.debian.org**
   - **原因**：网络无法访问Debian官方软件源
   - **解决方案**：
     - 已自动配置阿里云镜像源，如果仍然失败，可以手动配置：
     ```bash
     # 方法1：使用代理
     docker build --build-arg HTTP_PROXY=http://your-proxy:port --build-arg HTTPS_PROXY=http://your-proxy:port -t video-downloader .
     
     # 方法2：检查网络连接
     ping mirrors.aliyun.com
     
     # 方法3：如果使用Docker Desktop，检查DNS设置
     ```
   - **注意**：Dockerfile已自动配置国内镜像源（阿里云），如果仍失败，请检查网络连接

2. **无法访问网页**
   - 检查防火墙是否开放5000端口
   - 确认容器是否正常运行：`docker ps`

3. **解析失败**
   - 检查输入的URL是否正确
   - 某些网站可能需要特定的User-Agent或Cookie

4. **下载API调用失败**
   - 确认下载API服务是否运行在 `192.168.1.52:8081`
   - 检查网络连接

5. **pip安装依赖失败**
   - Dockerfile已配置使用清华镜像源
   - 如果失败，会自动尝试豆瓣镜像源
   - 如果都失败，会使用默认PyPI源

## 许可证

MIT License





