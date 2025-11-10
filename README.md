# M3U8视频下载工具

一个基于Flask的Web应用，用于从网页中提取视频链接（M3U8、MP4等）并下载视频。

## 功能特点

- 🔍 **智能提取**: 使用Playwright自动从网页中提取视频链接（M3U8、MP4等）
- 📥 **多线程下载**: 支持多线程分段下载，类似IDM的下载方式
- 🎬 **视频格式支持**: 支持M3U8、MP4、MKV、AVI、FLV、WebM等多种视频格式
- 🌐 **Web界面**: 提供友好的Web界面，方便使用
- 🐳 **Docker支持**: 支持Docker部署，一键启动
- 🚀 **实时进度**: 实时显示下载进度、速度和剩余时间
- 📊 **下载管理**: 支持查看、删除、暂停、恢复下载任务
- 🔄 **断点续传**: 支持下载中断后继续下载，无需重新开始
- ⏸️ **暂停/恢复**: 支持暂停和恢复下载任务
- ➕ **手动添加**: 支持手动添加下载链接
- 🔗 **更新链接**: 支持更新失效的下载链接
- 🔀 **并发控制**: 智能管理并发下载任务，避免资源耗尽

## 系统要求

- Python 3.8+
- 或 Docker 和 Docker Compose
- Debian ARM (ARM64/ARMv7) 或 Linux/Windows/Mac

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
# 挂载下载目录到宿主机
docker run -d -p 5000:5000 \
  -v $(pwd)/downloads:/app/downloads \
  -e DOWNLOAD_DIR=./downloads \
  -e MAX_DOWNLOAD_THREADS=8 \
  -e MAX_CONCURRENT_DOWNLOADS=3 \
  --name video-downloader video-downloader
```

### 使用Python直接运行

1. 安装依赖：
```bash
pip install -r requirements.txt
```

2. 运行应用：
```bash
python app.py
```

3. 访问应用：
打开浏览器访问 `http://localhost:5000`

## 使用方法

### 1. 提取视频链接

#### 方法一：从网页提取
- 在输入框中输入视频网页的URL
- 点击"解析"按钮
- 系统会自动提取网页中的视频链接（M3U8、MP4等）

#### 方法二：手动添加
- 点击"下载管理"链接
- 在"手动添加下载"表单中输入视频URL
- 可选择输入视频名称（留空则自动从URL提取）
- 点击"添加下载"按钮

### 2. 选择视频并下载

从解析结果中选择要下载的视频，点击"开始下载"按钮

### 3. 管理下载任务

点击"下载管理"链接，可以：
- 查看所有下载任务的进度、速度和剩余时间
- 暂停正在下载的任务
- 恢复暂停的任务（支持断点续传）
- 取消下载任务
- 删除已完成或失败的任务
- 更新失效的下载链接（对于失败的任务）
- 下载已完成的任务文件

## API接口

### 1. 解析URL

**请求:**
```http
POST /api/parse
Content-Type: application/json

{
  "url": "https://example.com/video"
}
```

**响应:**
```json
{
  "success": true,
  "videos": [
    {
      "url": "https://example.com/video.m3u8",
      "name": "视频名称",
      "type": "M3U8",
      "content_type": "application/vnd.apple.mpegurl"
    }
  ],
  "page_title": "页面标题"
}
```

### 2. 开始下载

**请求:**
```http
POST /api/download
Content-Type: application/json

{
  "url": "https://example.com/video.m3u8",
  "name": "视频名称",
  "type": "M3U8",
  "useragent": "Mozilla/5.0...",
  "outputformat": "mp4",
  "dir": "downloads",
  "referer": "https://example.com"
}
```

**响应:**
```json
{
  "success": true,
  "task_id": "abc123",
  "message": "下载任务已启动"
}
```

### 3. 手动添加下载

**请求:**
```http
POST /api/download/manual
Content-Type: application/json

{
  "url": "https://example.com/video.m3u8",
  "name": "视频名称（可选）",
  "useragent": "Mozilla/5.0...（可选）",
  "outputformat": "mp4（可选）",
  "dir": "downloads（可选）",
  "referer": "https://example.com（可选）"
}
```

**响应:**
```json
{
  "success": true,
  "task_id": "abc123",
  "message": "下载任务已启动"
}
```

### 4. 获取下载状态

**请求:**
```http
GET /api/download/<task_id>
```

**响应:**
```json
{
  "task_id": "abc123",
  "status": "downloading",
  "progress": 45.5,
  "downloaded": 10485760,
  "total_size": 23068672,
  "speed": 1024000,
  "eta": 12.3,
  "downloaded_segments": 45,
  "total_segments": 100
}
```

**状态说明:**
- `pending`: 等待中（在下载队列中）
- `downloading`: 下载中
- `paused`: 已暂停
- `completed`: 已完成
- `failed`: 失败
- `cancelled`: 已取消

### 5. 列出所有下载任务

**请求:**
```http
GET /api/downloads
```

**响应:**
```json
{
  "tasks": [
    {
      "task_id": "abc123",
      "name": "视频名称",
      "url": "https://example.com/video.m3u8",
      "status": "downloading",
      "progress": 45.5,
      "downloaded": 10485760,
      "total_size": 23068672,
      "speed": 1024000,
      "eta": 12.3,
      "start_time": 1234567890,
      "end_time": null,
      "error": null
    }
  ]
}
```

### 6. 暂停下载任务

**请求:**
```http
POST /api/download/<task_id>/pause
```

**响应:**
```json
{
  "success": true,
  "message": "下载任务已暂停"
}
```

### 7. 恢复下载任务

**请求:**
```http
POST /api/download/<task_id>/resume
```

**响应:**
```json
{
  "success": true,
  "message": "下载任务已恢复"
}
```

**注意:** 恢复下载支持断点续传，会从上次中断的地方继续下载。

### 8. 更新下载URL

**请求:**
```http
POST /api/download/<task_id>/update_url
Content-Type: application/json

{
  "url": "https://example.com/new-video.m3u8"
}
```

**响应:**
```json
{
  "success": true,
  "message": "URL已更新，从 https://example.com/old-video.m3u8 更新为 https://example.com/new-video.m3u8"
}
```

**注意:** 只有失败、暂停或已取消的任务才能更新URL。更新URL后，如果任务处于失败状态，会自动恢复下载。

### 9. 删除下载任务

**请求:**
```http
DELETE /api/download/<task_id>
```

**响应:**
```json
{
  "success": true,
  "message": "任务已删除"
}
```

**注意:** 删除任务会同时删除下载的文件和临时文件。

### 10. 下载文件

**请求:**
```http
GET /api/download/<task_id>/file
```

**响应:** 返回下载的文件（仅限已完成的任务）

## 配置说明

### 环境变量

- `DOWNLOAD_DIR`: 下载目录（默认: `./downloads`）
- `MAX_DOWNLOAD_THREADS`: 每个任务的最大下载线程数（默认: `8`）
- `MAX_CONCURRENT_DOWNLOADS`: 最大并发下载任务数（默认: `3`）
- `FLASK_ENV`: Flask环境（默认: `production`）

### 配置说明

- **DOWNLOAD_DIR**: 下载文件的保存目录
- **MAX_DOWNLOAD_THREADS**: 每个下载任务使用的线程数，影响单个任务的下载速度
- **MAX_CONCURRENT_DOWNLOADS**: 同时进行的下载任务数量，避免资源耗尽。如果超过此限制，新任务会进入队列等待

### 修改配置

1. **使用环境变量文件（推荐）**：
   ```bash
   # 创建.env文件
   echo "DOWNLOAD_DIR=./downloads" > .env
   echo "MAX_DOWNLOAD_THREADS=8" >> .env
   echo "MAX_CONCURRENT_DOWNLOADS=3" >> .env
   
   # 启动应用
   python app.py
   ```

2. **使用Docker Compose配置**：
   
   在 `docker-compose.yml` 中已经配置了环境变量支持：
   ```yaml
   environment:
     - DOWNLOAD_DIR=${DOWNLOAD_DIR:-./downloads}
     - MAX_DOWNLOAD_THREADS=${MAX_DOWNLOAD_THREADS:-8}
     - MAX_CONCURRENT_DOWNLOADS=${MAX_CONCURRENT_DOWNLOADS:-3}
   volumes:
     - ./downloads:/app/downloads
   ```
   
   可以创建 `.env` 文件来配置（docker-compose会自动读取）：
   ```bash
   # 创建.env文件
   echo "DOWNLOAD_DIR=./downloads" > .env
   echo "MAX_DOWNLOAD_THREADS=8" >> .env
   echo "MAX_CONCURRENT_DOWNLOADS=3" >> .env
   
   # 启动服务
   docker-compose up -d
   ```

## 功能详解

### 断点续传

- 下载中断后，可以恢复下载，系统会自动从上次中断的地方继续
- 支持多线程下载的断点续传，每个下载片段独立续传
- 支持M3U8视频的断点续传，已下载的TS片段不会重复下载
- 进度信息保存在临时目录中，重启服务后仍可恢复

### 暂停/恢复

- 可以暂停正在下载的任务，释放系统资源
- 暂停的任务可以随时恢复，支持断点续传
- 暂停的任务不会丢失下载进度

### 并发控制

- 系统会自动管理并发下载任务，避免资源耗尽
- 超过最大并发数时，新任务会进入队列等待
- 当有任务完成时，队列中的任务会自动启动
- 可以通过 `MAX_CONCURRENT_DOWNLOADS` 环境变量调整并发数

### 多线程下载

- 每个下载任务使用多个线程同时下载，提高下载速度
- 对于大文件，系统会自动分段下载，最后合并
- 对于M3U8视频，系统会并行下载所有TS片段
- 可以通过 `MAX_DOWNLOAD_THREADS` 环境变量调整线程数

### 错误处理和重试

- 下载失败时，系统会自动重试（最多3次）
- 使用指数退避策略，避免频繁重试
- 失败的任务可以更新URL后继续下载
- 详细的错误信息会显示在任务列表中

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

### 1. 无法提取视频链接

- 检查网页URL是否正确
- 检查网络连接是否正常
- 查看浏览器控制台和服务器日志
- 某些网站可能需要特定的User-Agent或Referer

### 2. 下载失败

- 检查视频URL是否有效
- 检查网络连接是否稳定
- 检查磁盘空间是否充足
- 查看服务器日志获取详细错误信息
- 对于失效的链接，可以使用"更新URL"功能

### 3. 下载速度慢

- 检查网络带宽
- 增加 `MAX_DOWNLOAD_THREADS` 环境变量（但不要设置过大）
- 检查服务器资源使用情况
- 减少 `MAX_CONCURRENT_DOWNLOADS` 以释放资源给单个任务

### 4. 任务一直处于pending状态

- 检查当前下载任务数是否达到 `MAX_CONCURRENT_DOWNLOADS` 限制
- 等待其他任务完成，队列中的任务会自动启动
- 可以暂停一些任务以释放资源

### 5. 断点续传不工作

- 检查临时目录是否有写入权限
- 检查磁盘空间是否充足
- 确保任务没有被删除（删除任务会清理临时文件）

### 6. Docker构建失败：无法解析deb.debian.org

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

### 7. Playwright安装失败

- 确保系统已安装必要的依赖
- 对于Linux系统，可能需要安装额外的系统库
- 参考Playwright官方文档进行安装
- 在Docker中，Playwright会自动安装

### 8. pip安装依赖失败

- Dockerfile已配置使用清华镜像源
- 如果失败，会自动尝试豆瓣镜像源
- 如果都失败，会使用默认PyPI源

## 注意事项

1. **版权问题**: 请确保您有权下载和使用视频内容，遵守相关法律法规
2. **资源限制**: 下载大量视频可能会消耗大量带宽和存储空间
3. **服务器资源**: 并发下载会消耗CPU和内存资源，请根据服务器性能调整配置
4. **网络稳定性**: 不稳定的网络连接可能导致下载失败，建议使用稳定的网络环境
5. **磁盘空间**: 确保有足够的磁盘空间存储下载的视频文件
6. **临时文件**: 下载过程中的临时文件会占用额外空间，完成后会自动清理

## 更新日志

### v2.0.0
- ✨ 新增断点续传功能
- ✨ 新增暂停/恢复下载功能
- ✨ 新增手动添加下载链接功能
- ✨ 新增更新失效下载链接功能
- ✨ 新增并发下载控制
- ✨ 改进错误处理和重试机制
- ✨ 支持更多视频格式（MP4、MKV、AVI等）
- 🐛 修复多任务并发下载失败的问题
- 🐛 修复下载进度显示不准确的问题
- 📝 更新文档和API说明

### v1.0.0
- 🎉 初始版本发布
- ✨ 支持从网页提取M3U8链接
- ✨ 支持多线程下载
- ✨ 支持Web界面
- ✨ 支持Docker部署

## 许可证

MIT License
