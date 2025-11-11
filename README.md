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
- 🔐 **用户认证**: 简单的单用户登录功能，保护系统安全
- 📋 **解析队列**: 支持添加多个URL到解析队列，按优先级自动处理
- 📝 **解析历史**: 保存所有解析历史记录，方便查看和选择下载
- 🎯 **自动选择**: 支持前缀匹配规则，自动选择需要下载的视频
- 🏷️ **名字前缀**: 可以为所有解析出的视频名字添加统一前缀
- 🔄 **互斥执行**: 解析和下载任务互斥执行，避免网速冲突，提高解析成功率

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

4. 登录系统：
   - 默认用户名: `admin`
   - 默认密码: `admin123`
   - 可通过环境变量 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 修改
   - 生产环境建议在 `docker-compose.yml` 或 `.env` 文件中设置安全的密码

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
  -e ADMIN_USERNAME=admin \
  -e ADMIN_PASSWORD=your-password \
  -e SECRET_KEY=your-secret-key \
  --name video-downloader video-downloader
```

**注意**: 生产环境请务必修改 `ADMIN_PASSWORD` 和 `SECRET_KEY` 为安全的值。

### 使用Python直接运行

1. 安装依赖：
```bash
pip install -r requirements.txt
```

2. 安装Playwright浏览器（如果需要）：
```bash
python -m playwright install chromium
```

3. 运行应用：
```bash
python app.py
```

4. 访问应用：
打开浏览器访问 `http://localhost:5000`

5. 登录：
   - 默认用户名: `admin`
   - 默认密码: `admin123`
   - 可通过环境变量 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 修改

## 使用方法

### 1. 登录系统

首次访问需要登录：
- 默认用户名: `admin`
- 默认密码: `admin123`
- 可在环境变量中配置 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD`

### 2. 提取视频链接

#### 方法一：从网页提取（实时解析）
- 在主页输入框中输入视频网页的URL
- 点击"解析M3U8链接"按钮
- 系统会自动提取网页中的视频链接（M3U8、MP4等）
- 从解析结果中选择要下载的视频，点击"开始下载"按钮

#### 方法二：解析队列（批量解析）
- 点击"解析队列"链接
- 在"添加解析任务"表单中输入视频网页URL
- 设置优先级（数字越小优先级越高，默认999）
- 点击"添加到队列"按钮
- 系统会按优先级自动处理队列中的任务
- 解析完成后，在"解析历史"中查看结果
- 可以从历史记录中直接启动下载

#### 方法三：手动添加下载
- 点击"下载管理"链接
- 在"手动添加下载"表单中输入视频URL
- 可选择输入视频名称（留空则自动从URL提取）
- 点击"添加下载"按钮

### 3. 解析队列管理

在"解析队列"页面可以：
- 查看所有待解析、处理中、已完成和失败的任务
- 调整任务的优先级（仅限待解析任务）
- 删除队列任务
- 查看解析历史记录（包含视频名称、类型和下载地址）
- 从历史记录中启动下载
- 删除历史记录

### 4. 自动选择配置

在"解析队列"页面可以配置：
- **前缀匹配规则**: 设置多个URL前缀，解析完成后自动选择匹配的视频并自动开始下载
  - 例如：设置前缀 `["https://example.com/video1", "https://example.com/video2"]`，系统会自动选择下载地址（URL）以这些前缀开头的视频
  - **注意**: 匹配是基于视频的下载地址（URL）前缀，而不是文件名
  - **自动下载机制**: 
    - 系统采用**解析和下载互斥执行**的策略，确保解析和下载不同时进行
    - 当设置有自动下载时，系统会先解析完所有队列中的链接
    - 当解析队列为空时，系统会一次性启动所有自动选择的下载任务
    - 当下载队列正在下载的视频为空时，系统才会开始处理新的解析任务
    - 这样可以避免下载占用网速导致解析失败的问题
- **视频名字前缀**: 为所有解析出的视频名字添加统一前缀
  - 例如：设置前缀 `[下载]`，所有视频名字都会变成 `[下载]视频名称`

### 5. 管理下载任务

点击"下载管理"链接，可以：
- 查看所有下载任务的进度、速度和剩余时间
- 暂停正在下载的任务
- 恢复暂停的任务（支持断点续传）
- 取消下载任务
- 删除已完成或失败的任务
- 更新失效的下载链接（对于失败的任务）
- 下载已完成的任务文件

## API接口

### 1. 登录

**请求:**
```http
POST /login
Content-Type: application/json

{
  "username": "admin",
  "password": "admin123"
}
```

**响应:**
```json
{
  "success": true,
  "message": "登录成功"
}
```

### 2. 登出

**请求:**
```http
POST /logout
```

**响应:**
```json
{
  "success": true,
  "message": "已登出"
}
```

### 3. 解析URL

**请求:**
```http
POST /api/parse
Content-Type: application/json

{
  "url": "https://example.com/video"
}
```

**注意:** 需要先登录，所有API接口都需要登录认证。

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

### 4. 开始下载

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

### 5. 手动添加下载

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

### 6. 获取下载状态

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

### 7. 列出所有下载任务

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

### 8. 暂停下载任务

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

### 9. 恢复下载任务

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

### 10. 更新下载URL

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

### 11. 删除下载任务

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

### 12. 下载文件

**请求:**
```http
GET /api/download/<task_id>/file
```

**响应:** 返回下载的文件（仅限已完成的任务）

### 13. 解析队列管理

#### 获取解析队列

**请求:**
```http
GET /api/parse-queue
```

**响应:**
```json
{
  "success": true,
  "queue": [
    {
      "id": 1,
      "url": "https://example.com/video",
      "priority": 1,
      "status": "pending",
      "created_at": 1234567890
    }
  ]
}
```

#### 添加解析队列任务

**请求:**
```http
POST /api/parse-queue
Content-Type: application/json

{
  "url": "https://example.com/video",
  "priority": 1
}
```

**响应:**
```json
{
  "success": true,
  "queue_id": 1,
  "message": "已添加到解析队列"
}
```

#### 删除解析队列任务

**请求:**
```http
DELETE /api/parse-queue/<queue_id>
```

**响应:**
```json
{
  "success": true,
  "message": "已删除"
}
```

#### 更新解析队列任务优先级

**请求:**
```http
POST /api/parse-queue/<queue_id>/priority
Content-Type: application/json

{
  "priority": 1
}
```

**响应:**
```json
{
  "success": true,
  "message": "优先级已更新"
}
```

### 14. 解析历史记录

#### 获取解析历史

**请求:**
```http
GET /api/parse-history
```

**响应:**
```json
{
  "success": true,
  "history": [
    {
      "queue_id": 1,
      "url": "https://example.com/video",
      "videos": [
        {
          "url": "https://example.com/video.m3u8",
  "name": "视频名称",
          "type": "M3U8",
          "content_type": "application/vnd.apple.mpegurl"
        }
      ],
      "selected_videos": [],
      "page_title": "页面标题",
      "parsed_at": 1234567890,
      "status": "success"
    }
  ]
}
```

**注意**: 历史记录中的 `videos` 数组包含每个视频的完整信息，包括：
- `url`: 视频的下载地址（用于前缀匹配和下载）
- `name`: 视频名称
- `type`: 视频类型（M3U8、MP4等）
- `content_type`: Content-Type头信息

#### 获取解析历史详情

**请求:**
```http
GET /api/parse-history/<queue_id>
```

**响应:**
```json
{
  "success": true,
  "record": {
    "url": "https://example.com/video",
    "videos": [...],
    "selected_videos": [...],
    "page_title": "页面标题",
    "parsed_at": 1234567890,
    "status": "success"
  }
}
```

#### 删除解析历史

**请求:**
```http
DELETE /api/parse-history/<queue_id>
```

**响应:**
```json
{
  "success": true,
  "message": "已删除"
}
```

### 15. 自动选择配置

#### 获取自动选择前缀列表

**请求:**
```http
GET /api/auto-select
```

**响应:**
```json
{
  "success": true,
  "prefixes": ["https://example.com/video1", "https://example.com/video2"]
}
```

**注意**: 返回的前缀列表是URL前缀，用于匹配视频的下载地址。

#### 设置自动选择前缀列表

**请求:**
```http
POST /api/auto-select
Content-Type: application/json

{
  "prefixes": ["https://example.com/video1", "https://example.com/video2"]
}
```

**响应:**
```json
{
  "success": true,
  "message": "前缀列表已更新"
}
```

**注意**: 
- 前缀匹配基于视频的下载地址（URL），而不是文件名
- 例如：设置前缀 `["https://example.com/video1"]`，会自动选择所有URL以 `https://example.com/video1` 开头的视频
- 匹配区分大小写

### 16. 视频名字前缀配置

#### 获取视频名字前缀

**请求:**
```http
GET /api/video-name-prefix
```

**响应:**
```json
{
  "success": true,
  "prefix": "[下载]"
}
```

#### 设置视频名字前缀

**请求:**
```http
POST /api/video-name-prefix
Content-Type: application/json

{
  "prefix": "[下载]"
}
```

**响应:**
```json
{
  "success": true,
  "message": "前缀已更新"
}
```

## 配置说明

### 环境变量

- `DOWNLOAD_DIR`: 下载目录（默认: `./downloads`）
- `MAX_DOWNLOAD_THREADS`: 每个任务的最大下载线程数（默认: `8`）
- `MAX_CONCURRENT_DOWNLOADS`: 最大并发下载任务数（默认: `3`）
- `FLASK_ENV`: Flask环境（默认: `production`）
- `ADMIN_USERNAME`: 管理员用户名（默认: `admin`）
- `ADMIN_PASSWORD`: 管理员密码（默认: `admin123`）
- `VIDEO_NAME_PREFIX`: 视频名字前缀（默认: 空）
- `SECRET_KEY`: Flask session密钥（默认: `your-secret-key-change-this-in-production`，生产环境请更改）

### 配置说明

- **DOWNLOAD_DIR**: 下载文件的保存目录
- **MAX_DOWNLOAD_THREADS**: 每个下载任务使用的线程数，影响单个任务的下载速度
- **MAX_CONCURRENT_DOWNLOADS**: 同时进行的下载任务数量，避免资源耗尽。如果超过此限制，新任务会进入队列等待
- **ADMIN_USERNAME**: 登录用户名，生产环境请修改
- **ADMIN_PASSWORD**: 登录密码，生产环境请修改为强密码
- **VIDEO_NAME_PREFIX**: 所有解析出的视频名字会自动添加此前缀
- **SECRET_KEY**: Flask session加密密钥，生产环境请使用随机字符串

### 修改配置

1. **使用环境变量文件（推荐）**：
   ```bash
   # 创建.env文件
   echo "DOWNLOAD_DIR=./downloads" > .env
   echo "MAX_DOWNLOAD_THREADS=8" >> .env
   echo "MAX_CONCURRENT_DOWNLOADS=3" >> .env
   echo "ADMIN_USERNAME=admin" >> .env
   echo "ADMIN_PASSWORD=your-secure-password" >> .env
   echo "SECRET_KEY=your-random-secret-key" >> .env
   echo "VIDEO_NAME_PREFIX=[下载]" >> .env
   
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
     - ADMIN_USERNAME=${ADMIN_USERNAME:-admin}
     - ADMIN_PASSWORD=${ADMIN_PASSWORD:-admin123}
     - SECRET_KEY=${SECRET_KEY:-your-secret-key-change-this-in-production}
     - VIDEO_NAME_PREFIX=${VIDEO_NAME_PREFIX:-}
   volumes:
     - ./downloads:/app/downloads
   ```
   
   可以创建 `.env` 文件来配置（docker-compose会自动读取）：
   ```bash
   # 创建.env文件
   echo "DOWNLOAD_DIR=./downloads" > .env
   echo "MAX_DOWNLOAD_THREADS=8" >> .env
   echo "MAX_CONCURRENT_DOWNLOADS=3" >> .env
   echo "ADMIN_USERNAME=admin" >> .env
   echo "ADMIN_PASSWORD=your-secure-password" >> .env
   echo "SECRET_KEY=your-random-secret-key" >> .env
   echo "VIDEO_NAME_PREFIX=[下载]" >> .env
   
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

### 解析和下载互斥执行

- **互斥机制**: 解析任务和下载任务同时只能有一个执行
  - 当有正在下载的任务时，系统会等待下载完成后再处理解析任务
  - 当有正在处理的解析任务时，系统会等待解析完成后再启动下载任务
- **自动下载流程**:
  1. 系统先解析完所有队列中的链接
  2. 为自动选择的视频创建下载任务（但不立即启动）
  3. 当解析队列为空时，一次性启动所有自动选择的下载任务
  4. 当下载队列为空时，系统会继续处理新的解析任务
- **优势**: 
  - 避免下载占用网速导致解析失败
  - 提高解析成功率
  - 确保网络资源合理分配

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

### 用户认证

- 简单的单用户登录系统，保护系统安全
- 使用Flask session管理登录状态
- 所有API接口和页面都需要登录认证
- 支持通过环境变量配置用户名和密码
- 生产环境建议修改默认密码和SECRET_KEY

### 解析队列

- 支持批量添加URL到解析队列
- 按优先级自动处理队列中的任务（数字越小优先级越高）
- 可以调整任务的优先级
- 可以删除队列中的任务
- **解析和下载互斥执行**:
  - 当有正在下载的任务时，系统会等待下载完成后再处理解析任务
  - 当有正在处理的解析任务时，系统会等待解析完成后再启动下载任务
  - 这样可以避免下载占用网速导致解析失败
- 解析队列和历史记录存储在内存中，重启后会丢失

### 解析历史

- 自动保存所有解析结果
- 可以查看历史记录中的视频列表
- 可以从历史记录中直接启动下载
- 可以删除历史记录
- 历史记录包含解析时间、状态、视频列表等信息

### 自动选择与自动下载

- 支持配置多个URL前缀匹配规则
- 解析完成后自动选择匹配的视频
- **自动下载机制**: 当设置有自动下载时，系统会采用**解析和下载互斥执行**的策略
  - **解析优先**: 系统会先解析完所有队列中的链接，创建下载任务但不启动
  - **批量启动**: 当解析队列为空时，系统会一次性启动所有自动选择的下载任务
  - **互斥执行**: 解析任务和下载任务同时只能有一个执行
    - 当有正在下载的任务时，系统会等待下载完成后再处理解析任务
    - 当有正在处理的解析任务时，系统会等待解析完成后再启动下载任务
  - **优势**: 避免下载占用网速导致解析失败，提高解析成功率
- 匹配规则基于视频的下载地址（URL）前缀，而不是文件名
- 匹配区分大小写
- 可以在解析队列页面配置和管理
- 例如：设置前缀 `["https://example.com/video1"]`，会自动选择所有URL以该前缀开头的视频，并在解析队列为空时自动开始下载

### 视频名字前缀

- 可以为所有解析出的视频名字添加统一前缀
- 前缀会应用到所有通过解析队列和实时解析获得的视频
- 可以在解析队列页面配置
- 支持通过环境变量 `VIDEO_NAME_PREFIX` 配置

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

### 10. Playwright安装失败

- 确保系统已安装必要的依赖
- 对于Linux系统，可能需要安装额外的系统库
- 参考Playwright官方文档进行安装
- 在Docker中，Playwright会自动安装
- 如果安装失败，可以手动运行: `python -m playwright install chromium`

### 11. pip安装依赖失败

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
7. **安全配置**: 生产环境请务必修改默认用户名和密码，以及SECRET_KEY
8. **解析队列**: 解析队列和历史记录存储在内存中，重启服务后会丢失
9. **会话管理**: 使用Flask session管理登录状态，默认使用内存存储，重启后会失效
10. **数据持久化**: 如需持久化解析队列和历史记录，建议扩展为使用数据库存储
11. **互斥执行**: 解析和下载任务互斥执行，当有下载任务时会暂停解析，当有解析任务时会暂停下载，这是正常行为
12. **自动下载**: 当设置有自动下载时，系统会先解析完所有链接，然后一次性启动所有下载任务

## 更新日志

### v3.0.0
- ✨ 新增用户登录功能（单用户认证）
- ✨ 新增解析队列功能（批量添加URL，按优先级处理）
- ✨ 新增解析历史记录功能（保存所有解析结果）
- ✨ 新增自动选择功能（基于URL前缀匹配规则）
- ✨ 新增自动下载功能（自动选择后自动开始下载）
- ✨ 新增解析和下载互斥执行机制（确保解析和下载不同时进行，避免网速冲突）
- ✨ 新增视频名字前缀功能（统一添加前缀）
- ✨ 新增队列任务优先级调整功能
- ✨ 新增从历史记录启动下载功能
- 🔐 所有API接口需要登录认证
- 📝 更新文档和API说明

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
