from flask import Flask, render_template, request, jsonify, send_from_directory
import requests
import re
import json
import time
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin
import hashlib
from pathlib import Path
import shutil

app = Flask(__name__)

# 下载配置
DOWNLOAD_DIR = os.environ.get('DOWNLOAD_DIR', './downloads')
TEMP_DIR = os.path.join(DOWNLOAD_DIR, 'temp')
MAX_WORKERS = int(os.environ.get('MAX_DOWNLOAD_THREADS', '8'))  # 默认8线程
CHUNK_SIZE = 1024 * 1024  # 1MB chunks

# 创建下载目录
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# 下载任务管理（内存存储，可扩展为数据库）
download_tasks = {}
download_lock = threading.Lock()

# 下载线程管理（限制同时下载的任务数）
MAX_CONCURRENT_DOWNLOADS = int(os.environ.get('MAX_CONCURRENT_DOWNLOADS', '3'))  # 默认同时下载3个任务
active_download_threads = {}  # 存储活动的下载线程
download_thread_lock = threading.Lock()

# 设置环境变量以确保在无GUI系统中正常运行
if 'DISPLAY' not in os.environ:
    os.environ['DISPLAY'] = ':99'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'


def extract_video_from_network_requests(url):
    """通过Playwright捕获网络请求来获取所有视频链接（包括m3u8和其他视频格式）"""
    video_urls = []  # 存储所有视频URL，格式: {'url': url, 'type': type, 'content_type': content_type}
    page_title = "未知视频"
    
    # 视频文件扩展名列表
    video_extensions = ['.m3u8', '.mp4', '.mkv', '.avi', '.flv', '.ts', '.webm', '.mov', '.wmv', '.m4v', '.f4v']
    
    # 视频Content-Type列表
    video_content_types = [
        'video/',
        'application/vnd.apple.mpegurl',  # m3u8
        'application/x-mpegURL',  # m3u8
        'audio/mpegurl',  # m3u8
    ]
    
    try:
        from playwright.sync_api import sync_playwright
        
        print(f"使用Playwright获取视频链接...")
        
        with sync_playwright() as p:
            # 启动浏览器（headless模式，适用于无GUI系统）
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-accelerated-2d-canvas',
                    '--disable-gpu',
                    '--disable-software-rasterizer',
                    '--disable-extensions',
                    '--no-first-run',
                    '--no-zygote',
                    '--single-process',
                ]
            )
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080}
            )
            page = context.new_page()
            
            # 收集所有网络请求和响应中的视频链接
            captured_urls = {}  # 使用字典存储，key为URL，value为{'type': type, 'content_type': content_type}
            
            def handle_request(request):
                """捕获请求URL"""
                request_url = request.url
                # 检查URL是否包含视频扩展名
                url_lower = request_url.lower()
                for ext in video_extensions:
                    if ext in url_lower:
                        # 根据扩展名判断视频类型
                        video_type = ext.replace('.', '').upper()
                        if ext == '.m3u8':
                            video_type = 'M3U8'
                        elif ext == '.ts':
                            video_type = 'TS'
                        captured_urls[request_url] = {
                            'type': video_type,
                            'content_type': None,  # 请求时还没有content-type
                            'source': 'request'
                        }
                        print(f"从请求中捕获视频URL: {request_url[:100]}... (类型: {video_type})")
                        break
            
            def handle_response(response):
                """捕获响应URL和Content-Type"""
                response_url = response.url
                try:
                    content_type = response.headers.get('content-type', '')
                    content_type_lower = content_type.lower() if content_type else ''
                    
                    # 检查Content-Type是否是视频类型
                    is_video = False
                    video_type = None
                    
                    # 检查是否是视频Content-Type
                    for vct in video_content_types:
                        if vct in content_type_lower:
                            is_video = True
                            if 'mpegurl' in content_type_lower or 'm3u8' in content_type_lower:
                                video_type = 'M3U8'
                            elif 'mp4' in content_type_lower:
                                video_type = 'MP4'
                            elif 'webm' in content_type_lower:
                                video_type = 'WEBM'
                            elif 'quicktime' in content_type_lower or 'mov' in content_type_lower:
                                video_type = 'MOV'
                            else:
                                video_type = content_type.split('/')[1].upper() if '/' in content_type else 'VIDEO'
                            break
                    
                    # 如果没有通过Content-Type识别，检查URL扩展名
                    if not is_video:
                        url_lower = response_url.lower()
                        for ext in video_extensions:
                            if ext in url_lower:
                                is_video = True
                                video_type = ext.replace('.', '').upper()
                                if ext == '.m3u8':
                                    video_type = 'M3U8'
                                elif ext == '.ts':
                                    video_type = 'TS'
                                break
                    
                    if is_video:
                        # 如果URL已存在，更新content-type；否则添加新URL
                        if response_url in captured_urls:
                            captured_urls[response_url]['content_type'] = content_type
                        else:
                            captured_urls[response_url] = {
                                'type': video_type or 'VIDEO',
                                'content_type': content_type,
                                'source': 'response'
                            }
                        print(f"从响应中捕获视频URL: {response_url[:100]}... (类型: {video_type or 'VIDEO'}, Content-Type: {content_type})")
                        
                except Exception as e:
                    print(f"处理响应时出错: {e}")
            
            # 注册请求和响应处理器
            page.on("request", handle_request)
            page.on("response", handle_response)
            
            # 过滤资源以加快加载（只加载必要的资源）
            def handle_route(route):
                """过滤资源，只加载必要的资源"""
                resource_type = route.request.resource_type
                # 允许文档、脚本、样式表、xhr、fetch、websocket、media（视频、音频）
                if resource_type in ['document', 'script', 'stylesheet', 'xhr', 'fetch', 'websocket', 'media']:
                    route.continue_()
                # 阻止图片、字体等
                elif resource_type in ['image', 'font', 'texttrack']:
                    route.abort()
                else:
                    route.continue_()
            
            try:
                page.route("**/*", handle_route)
                print("已启用资源过滤以加快加载")
            except:
                print("资源过滤启用失败，继续执行...")
            
            try:
                # 访问页面 - 使用更实用的等待策略
                print(f"正在访问页面: {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                print("页面DOM加载完成")
                
                # 等待JavaScript执行
                time.sleep(3)
                
                # 尝试关闭弹窗
                try:
                    page.evaluate("""
                        () => {
                            // 关闭常见的弹窗
                            const popups = document.querySelectorAll('.ds-pop, [class*="ds-pop"], .pop-overlay, .modal-overlay, .overlay, [class*="overlay"], .modal, .popup, [class*="modal"], [class*="popup"]');
                            popups.forEach(popup => {
                                if (popup) {
                                    popup.style.display = 'none';
                                    popup.remove();
                                }
                            });
                            
                            // 关闭通知
                            const notices = document.querySelectorAll('#notice, [id*="notice"]');
                            notices.forEach(notice => {
                                if (notice) {
                                    notice.style.display = 'none';
                                    notice.remove();
                                }
                            });
                            
                            // 触发ESC键
                            document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', keyCode: 27 }));
                            document.dispatchEvent(new KeyboardEvent('keyup', { key: 'Escape', keyCode: 27 }));
                        }
                    """)
                    print("已尝试关闭弹窗")
                except Exception as e:
                    print(f"关闭弹窗时出错: {e}")
                
                # 尝试点击播放按钮
                try:
                    play_button = page.query_selector('button[class*="play"], .play-button, [id*="play"], video')
                    if play_button:
                        print("找到播放按钮，尝试点击...")
                        try:
                            play_button.click(timeout=5000)
                            print("播放按钮点击成功")
                            time.sleep(2)
                        except:
                            # 如果普通点击失败，尝试强制点击
                            try:
                                page.evaluate("""
                                    () => {
                                        const playBtn = document.querySelector('button[class*="play"], .play-button, [id*="play"], video');
                                        if (playBtn) {
                                            playBtn.click();
                                        }
                                    }
                                """)
                                print("通过JavaScript点击播放按钮")
                                time.sleep(2)
                            except:
                                print("播放按钮点击失败，继续执行...")
                    else:
                        print("未找到播放按钮")
                except Exception as e:
                    print(f"查找播放按钮失败: {e}")
                
                # 等待网络请求
                print("等待网络请求...")
                time.sleep(8)
                
                # 获取页面标题
                try:
                    page_title = page.title()
                    print(f"获取到页面标题: {page_title}")
                except Exception as e:
                    print(f"获取页面标题失败: {e}")
                
            except Exception as e:
                print(f"访问页面失败: {e}")
            finally:
                browser.close()
            
            # 将捕获的URL转换为列表格式
            for url_item, info in captured_urls.items():
                video_urls.append({
                    'url': url_item,
                    'type': info['type'],
                    'content_type': info.get('content_type', ''),
                    'source': info['source']
                })
            
            print(f"共捕获到 {len(video_urls)} 个视频链接")
    except ImportError:
        print("Playwright未安装，无法获取视频链接")
        import traceback
        traceback.print_exc()
    except Exception as e:
        print(f"Playwright获取视频链接失败: {e}")
        import traceback
        traceback.print_exc()
    
    # 格式化视频链接列表，使用页面标题
    formatted_links = []
    for idx, video_info in enumerate(video_urls):
        url_item = video_info['url']
        video_type = video_info['type']
        content_type = video_info.get('content_type', '')
        
        # 生成视频名称
        if len(video_urls) > 1:
            name = f"{page_title} - {video_type}视频 {idx+1}" if page_title and page_title != "未知视频" else f"{video_type}视频 {idx+1}"
        else:
            name = f"{page_title} ({video_type})" if page_title and page_title != "未知视频" else f"{video_type}视频 1"
        
        formatted_links.append({
            'url': url_item,
            'name': name,
            'type': video_type,
            'content_type': content_type
        })
    
    return formatted_links, page_title


@app.route('/')
def index():
    """主页"""
    return render_template('index.html')


@app.route('/downloads')
def downloads_page():
    """下载管理页面"""
    return render_template('downloads.html')


@app.route('/api/parse', methods=['POST'])
def parse_url():
    """解析网址获取视频链接（包括m3u8和其他视频格式）"""
    try:
        data = request.get_json()
        url = data.get('url', '').strip()
        
        if not url:
            return jsonify({'error': 'URL不能为空'}), 400
        
        # 添加协议如果缺失
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        # 使用Playwright获取所有视频链接
        video_links, page_title = extract_video_from_network_requests(url)
        
        if not video_links:
            return jsonify({
                'error': '未找到视频链接',
                'title': page_title,
                'suggestion': '视频链接可能通过JavaScript动态加载，请确保已安装playwright'
            }), 404
        
        return jsonify({
            'success': True,
            'm3u8_list': video_links,  # 保持字段名兼容性，但实际包含所有视频类型
            'page_title': page_title
        })
    
    except Exception as e:
        print(f"解析失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'解析失败: {str(e)}'}), 500


def generate_task_id(url, name):
    """生成任务ID"""
    content = f"{url}_{name}_{time.time()}"
    return hashlib.md5(content.encode()).hexdigest()[:12]


def get_file_size(url, headers=None, max_retries=3):
    """获取文件大小（带重试）"""
    for attempt in range(max_retries):
        try:
            response = requests.head(url, headers=headers, timeout=10, allow_redirects=True, verify=False)
            if response.status_code == 200:
                content_length = response.headers.get('content-length')
                if content_length:
                    return int(content_length)
            # 如果HEAD不支持，尝试GET
            response = requests.get(url, headers=headers, timeout=10, stream=True, verify=False)
            if response.status_code == 200:
                content_length = response.headers.get('content-length')
                if content_length:
                    return int(content_length)
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"获取文件大小失败: {e}")
            else:
                time.sleep(1)  # 重试前等待
    return None


def save_download_progress(task_id, progress_data):
    """保存下载进度到文件"""
    try:
        progress_file = os.path.join(TEMP_DIR, f'{task_id}.progress.json')
        with open(progress_file, 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"保存下载进度失败: {e}")


def load_download_progress(task_id):
    """从文件加载下载进度"""
    try:
        progress_file = os.path.join(TEMP_DIR, f'{task_id}.progress.json')
        if os.path.exists(progress_file):
            with open(progress_file, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        print(f"加载下载进度失败: {e}")
    return None


def delete_download_progress(task_id):
    """删除下载进度文件"""
    try:
        progress_file = os.path.join(TEMP_DIR, f'{task_id}.progress.json')
        if os.path.exists(progress_file):
            os.remove(progress_file)
    except Exception as e:
        print(f"删除下载进度失败: {e}")


def download_chunk(url, start, end, chunk_file, task_id, headers=None, max_retries=3):
    """下载文件片段（支持断点续传）"""
    for attempt in range(max_retries):
        try:
            # 检查任务状态
            with download_lock:
                if task_id not in download_tasks:
                    return False
                task_status = download_tasks[task_id].get('status')
                if task_status in ['cancelled', 'paused']:
                    return False
            
            # 检查文件是否已存在（断点续传）
            existing_size = 0
            if os.path.exists(chunk_file):
                existing_size = os.path.getsize(chunk_file)
                if existing_size >= (end - start + 1):
                    # 文件已完整下载
                    return True
            
            # 计算实际需要下载的范围
            actual_start = start + existing_size
            if actual_start > end:
                return True
            
            range_headers = headers.copy() if headers else {}
            range_headers['Range'] = f'bytes={actual_start}-{end}'
            
            # 使用更长的超时时间
            response = requests.get(url, headers=range_headers, stream=True, timeout=60, verify=False)
            
            # 处理206 Partial Content和200 OK
            if response.status_code not in [200, 206]:
                response.raise_for_status()
            
            # 追加模式写入（支持断点续传）
            mode = 'ab' if existing_size > 0 else 'wb'
            with open(chunk_file, mode) as f:
                chunk_downloaded = 0
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)
                        chunk_downloaded += len(chunk)
                        # 更新任务进度
                        with download_lock:
                            if task_id in download_tasks:
                                task_status = download_tasks[task_id].get('status')
                                if task_status in ['cancelled', 'paused']:
                                    return False
                                download_tasks[task_id]['downloaded'] += len(chunk)
            
            return True
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                print(f"下载片段失败 (已重试{max_retries}次): {e}")
                return False
            else:
                print(f"下载片段失败，第{attempt + 1}次重试: {e}")
                time.sleep(2 ** attempt)  # 指数退避
        except Exception as e:
            print(f"下载片段失败: {e}")
            return False
    return False


def download_file_multithread(url, output_path, task_id, headers=None, num_threads=8, resume=False):
    """多线程下载文件（支持断点续传）"""
    try:
        # 检查任务状态
        with download_lock:
            if task_id not in download_tasks:
                return False
            task_status = download_tasks[task_id].get('status')
            if task_status in ['cancelled', 'paused']:
                return False
        
        # 获取文件大小
        file_size = get_file_size(url, headers)
        
        # 更新任务总大小
        with download_lock:
            if task_id in download_tasks:
                if file_size:
                    download_tasks[task_id]['total_size'] = file_size
                else:
                    download_tasks[task_id]['total_size'] = download_tasks[task_id].get('total_size', 0)
        
        if not file_size:
            # 如果不能获取大小，使用单线程下载
            print("无法获取文件大小，使用单线程下载")
            return download_file_single(url, output_path, task_id, headers, resume)
        
        # 计算每个线程下载的字节范围
        chunk_size = file_size // num_threads
        if chunk_size < 1024 * 1024:  # 如果每个块小于1MB，减少线程数
            num_threads = max(1, file_size // (1024 * 1024))
            chunk_size = file_size // num_threads if num_threads > 0 else file_size
        
        chunks = []
        for i in range(num_threads):
            start = i * chunk_size
            if i == num_threads - 1:
                end = file_size - 1
            else:
                end = start + chunk_size - 1
            chunks.append((start, end))
        
        # 创建临时文件目录
        temp_dir = os.path.join(TEMP_DIR, task_id)
        os.makedirs(temp_dir, exist_ok=True)
        
        # 检查是否存在进度文件（断点续传）
        progress_data = load_download_progress(task_id) if resume else None
        chunk_files = []
        
        for i, (start, end) in enumerate(chunks):
            chunk_file = os.path.join(temp_dir, f'chunk_{i:04d}.tmp')
            chunk_files.append((chunk_file, start, end, i))
        
        # 如果恢复下载，检查已下载的片段
        if resume and progress_data:
            downloaded_chunks = progress_data.get('downloaded_chunks', [])
            total_downloaded = progress_data.get('downloaded', 0)
            with download_lock:
                if task_id in download_tasks:
                    download_tasks[task_id]['downloaded'] = total_downloaded
        
        # 多线程下载（限制重试次数）
        max_retries = 3
        for retry in range(max_retries):
            # 检查任务状态
            with download_lock:
                if task_id not in download_tasks:
                    return False
                task_status = download_tasks[task_id].get('status')
                if task_status in ['cancelled', 'paused']:
                    return False
            
            # 使用线程池下载
            executor = ThreadPoolExecutor(max_workers=num_threads)
            try:
                futures = []
                for chunk_file, start, end, chunk_idx in chunk_files:
                    # 检查是否已完整下载
                    if os.path.exists(chunk_file):
                        existing_size = os.path.getsize(chunk_file)
                        expected_size = end - start + 1
                        if existing_size >= expected_size:
                            continue  # 跳过已完成的片段
                    
                    future = executor.submit(download_chunk, url, start, end, chunk_file, task_id, headers)
                    futures.append((future, chunk_file, start, end))
                
                # 等待所有下载完成
                results = []
                for future, chunk_file, start, end in futures:
                    try:
                        result = future.result(timeout=300)  # 5分钟超时
                        results.append(result)
                    except Exception as e:
                        print(f"下载片段失败: {e}")
                        results.append(False)
                
                # 检查结果
                if all(results):
                    break  # 所有片段下载成功
                else:
                    failed_count = results.count(False)
                    print(f"部分片段下载失败: {failed_count}/{len(results)}")
                    if retry < max_retries - 1:
                        print(f"第{retry + 1}次重试...")
                        time.sleep(2)
            finally:
                executor.shutdown(wait=False)
        
        # 检查任务状态
        with download_lock:
            if task_id not in download_tasks:
                return False
            task_status = download_tasks[task_id].get('status')
            if task_status in ['cancelled', 'paused']:
                return False
        
        # 合并文件
        try:
            with open(output_path, 'wb') as outfile:
                for chunk_file, start, end, chunk_idx in sorted(chunk_files, key=lambda x: x[3]):
                    if os.path.exists(chunk_file):
                        chunk_size_actual = os.path.getsize(chunk_file)
                        expected_size = end - start + 1
                        
                        # 检查文件完整性
                        if chunk_size_actual < expected_size:
                            print(f"警告: 片段{chunk_idx}不完整 ({chunk_size_actual}/{expected_size} bytes)")
                        
                        with open(chunk_file, 'rb') as infile:
                            shutil.copyfileobj(infile, outfile)
                        
                        # 检查任务状态
                        with download_lock:
                            if task_id not in download_tasks:
                                return False
                            task_status = download_tasks[task_id].get('status')
                            if task_status in ['cancelled', 'paused']:
                                return False
        except Exception as e:
            print(f"合并文件失败: {e}")
            return False
        
        # 清理临时文件
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
            delete_download_progress(task_id)
        except Exception as e:
            print(f"清理临时文件失败: {e}")
        
        return True
    except Exception as e:
        print(f"多线程下载失败: {e}")
        import traceback
        traceback.print_exc()
        # 如果多线程失败，尝试单线程
        print("尝试单线程下载...")
        return download_file_single(url, output_path, task_id, headers, resume)


def download_file_single(url, output_path, task_id, headers=None, resume=False):
    """单线程下载文件（支持断点续传）"""
    try:
        # 检查任务状态
        with download_lock:
            if task_id not in download_tasks:
                return False
            task_status = download_tasks[task_id].get('status')
            if task_status in ['cancelled', 'paused']:
                return False
        
        # 检查是否支持断点续传
        existing_size = 0
        mode = 'wb'
        range_headers = headers.copy() if headers else {}
        
        if resume and os.path.exists(output_path):
            existing_size = os.path.getsize(output_path)
            if existing_size > 0:
                mode = 'ab'
                range_headers['Range'] = f'bytes={existing_size}-'
                print(f"断点续传: 从 {existing_size} 字节开始下载")
        
        response = requests.get(url, headers=range_headers, stream=True, timeout=60, verify=False)
        
        # 处理206 Partial Content（断点续传）和200 OK
        if response.status_code not in [200, 206]:
            response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        if total_size == 0 and existing_size == 0:
            # 无法获取总大小，尝试从Content-Range获取
            content_range = response.headers.get('content-range', '')
            if content_range:
                # Content-Range: bytes 0-1023/2048
                parts = content_range.split('/')
                if len(parts) == 2:
                    total_size = int(parts[1])
        
        # 更新总大小
        with download_lock:
            if task_id in download_tasks:
                if total_size > 0:
                    download_tasks[task_id]['total_size'] = total_size + existing_size
                download_tasks[task_id]['downloaded'] = existing_size
        
        with open(output_path, mode) as f:
            downloaded = existing_size
            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    # 更新任务进度
                    with download_lock:
                        if task_id in download_tasks:
                            task_status = download_tasks[task_id].get('status')
                            if task_status in ['cancelled', 'paused']:
                                return False
                            download_tasks[task_id]['downloaded'] = downloaded
                            if total_size > 0:
                                download_tasks[task_id]['total_size'] = total_size + existing_size
                    
                    # 定期保存进度
                    if downloaded % (10 * 1024 * 1024) == 0:  # 每10MB保存一次
                        save_download_progress(task_id, {
                            'downloaded': downloaded,
                            'total_size': total_size + existing_size,
                            'timestamp': time.time()
                        })
        
        return True
    except Exception as e:
        print(f"单线程下载失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def download_m3u8(url, output_path, task_id, headers=None, resume=False):
    """下载M3U8视频（支持多线程下载TS文件和断点续传）"""
    try:
        # 检查任务状态
        with download_lock:
            if task_id not in download_tasks:
                return False
            task_status = download_tasks[task_id].get('status')
            if task_status in ['cancelled', 'paused']:
                return False
        
        # 获取M3U8文件内容
        response = requests.get(url, headers=headers, timeout=30, verify=False)
        response.raise_for_status()
        m3u8_content = response.text
        
        # 解析M3U8文件，获取TS文件列表
        ts_urls = []
        base_url = '/'.join(url.split('/')[:-1]) + '/'
        
        for line in m3u8_content.split('\n'):
            line = line.strip()
            if line and not line.startswith('#'):
                if line.startswith('http'):
                    ts_urls.append(line)
                else:
                    ts_urls.append(urljoin(base_url, line))
        
        if not ts_urls:
            raise Exception("M3U8文件中没有找到TS文件")
        
        # 更新任务信息
        with download_lock:
            if task_id in download_tasks:
                download_tasks[task_id]['total_segments'] = len(ts_urls)
                if resume:
                    # 恢复下载时，检查已下载的片段数
                    downloaded_segments = download_tasks[task_id].get('downloaded_segments', 0)
                else:
                    download_tasks[task_id]['downloaded_segments'] = 0
        
        # 下载所有TS文件（使用多线程）
        temp_dir = os.path.join(TEMP_DIR, task_id)
        os.makedirs(temp_dir, exist_ok=True)
        
        ts_files = []
        for i, ts_url in enumerate(ts_urls):
            ts_file = os.path.join(temp_dir, f'segment_{i:05d}.ts')
            ts_files.append((i, ts_file, ts_url))
        
        def download_ts_segment(segment_info, max_retries=3):
            """下载单个TS片段（支持重试）"""
            segment_idx, ts_file, ts_url = segment_info
            
            # 检查文件是否已存在（断点续传）
            if resume and os.path.exists(ts_file):
                file_size = os.path.getsize(ts_file)
                if file_size > 0:
                    # 文件已存在，假设已下载完成（可以改进为验证文件完整性）
                    with download_lock:
                        if task_id in download_tasks:
                            download_tasks[task_id]['downloaded_segments'] += 1
                    return True
            
            for attempt in range(max_retries):
                try:
                    # 检查任务状态
                    with download_lock:
                        if task_id not in download_tasks:
                            return False
                        task_status = download_tasks[task_id].get('status')
                        if task_status in ['cancelled', 'paused']:
                            return False
                    
                    response = requests.get(ts_url, headers=headers, timeout=60, stream=True, verify=False)
                    response.raise_for_status()
                    
                    with open(ts_file, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                            if chunk:
                                f.write(chunk)
                                # 检查任务状态
                                with download_lock:
                                    if task_id not in download_tasks:
                                        return False
                                    task_status = download_tasks[task_id].get('status')
                                    if task_status in ['cancelled', 'paused']:
                                        return False
                    
                    # 更新进度
                    with download_lock:
                        if task_id in download_tasks:
                            download_tasks[task_id]['downloaded_segments'] += 1
                    
                    return True
                except Exception as e:
                    if attempt == max_retries - 1:
                        print(f"下载TS片段失败 {segment_idx} (已重试{max_retries}次): {e}")
                        return False
                    else:
                        print(f"下载TS片段失败 {segment_idx}，第{attempt + 1}次重试: {e}")
                        time.sleep(2 ** attempt)  # 指数退避
            
            return False
        
        # 使用多线程下载TS文件（限制并发数，避免资源耗尽）
        max_ts_workers = min(MAX_WORKERS, len(ts_files), 16)  # 最多16个并发
        
        # 分批下载，避免同时创建太多线程
        batch_size = max_ts_workers * 2
        all_results = []
        
        for batch_start in range(0, len(ts_files), batch_size):
            batch_end = min(batch_start + batch_size, len(ts_files))
            batch_files = ts_files[batch_start:batch_end]
            
            # 检查任务状态
            with download_lock:
                if task_id not in download_tasks:
                    return False
                task_status = download_tasks[task_id].get('status')
                if task_status in ['cancelled', 'paused']:
                    return False
            
            with ThreadPoolExecutor(max_workers=max_ts_workers) as executor:
                futures = [executor.submit(download_ts_segment, ts_info) for ts_info in batch_files]
                batch_results = []
                for future in as_completed(futures):
                    try:
                        result = future.result(timeout=300)  # 5分钟超时
                        batch_results.append(result)
                    except Exception as e:
                        print(f"下载TS片段异常: {e}")
                        batch_results.append(False)
                all_results.extend(batch_results)
        
        # 检查结果
        if not all(all_results):
            failed_count = all_results.count(False)
            print(f"部分TS片段下载失败: {failed_count}/{len(all_results)}")
            # 允许部分失败，只要成功超过一半就继续
            if failed_count > len(all_results) / 2:
                return False
        
        # 检查任务状态
        with download_lock:
            if task_id not in download_tasks:
                return False
            task_status = download_tasks[task_id].get('status')
            if task_status in ['cancelled', 'paused']:
                return False
        
        # 合并TS文件（按顺序）
        try:
            with open(output_path, 'wb') as outfile:
                for segment_idx, ts_file, _ in sorted(ts_files, key=lambda x: x[0]):
                    if os.path.exists(ts_file):
                        with open(ts_file, 'rb') as infile:
                            shutil.copyfileobj(infile, outfile)
                        # 检查任务状态
                        with download_lock:
                            if task_id not in download_tasks:
                                return False
                            task_status = download_tasks[task_id].get('status')
                            if task_status in ['cancelled', 'paused']:
                                return False
        except Exception as e:
            print(f"合并TS文件失败: {e}")
            return False
        
        # 清理临时文件
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
            delete_download_progress(task_id)
        except Exception as e:
            print(f"清理临时文件失败: {e}")
        
        return True
    except Exception as e:
        print(f"M3U8下载失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def download_task_worker(task_id, url, output_path, video_type, headers=None, num_threads=8, resume=False):
    """下载任务工作线程"""
    try:
        with download_lock:
            # 检查任务是否已被取消
            if task_id not in download_tasks:
                return
            task_status = download_tasks[task_id].get('status')
            if task_status == 'cancelled':
                return
            
            # 检查是否暂停
            if task_status == 'paused':
                # 等待恢复
                while task_status == 'paused':
                    download_lock.release()
                    time.sleep(1)
                    download_lock.acquire()
                    if task_id not in download_tasks:
                        return
                    task_status = download_tasks[task_id].get('status')
                    if task_status == 'cancelled':
                        return
            
            download_tasks[task_id]['status'] = 'downloading'
            if not download_tasks[task_id].get('start_time'):
                download_tasks[task_id]['start_time'] = time.time()
        
        # 检查是否支持断点续传
        can_resume = resume
        if resume:
            # 检查文件是否存在
            if os.path.exists(output_path):
                existing_size = os.path.getsize(output_path)
                if existing_size > 0:
                    can_resume = True
                    with download_lock:
                        if task_id in download_tasks:
                            download_tasks[task_id]['downloaded'] = existing_size
            else:
                # 检查临时文件
                temp_dir = os.path.join(TEMP_DIR, task_id)
                if os.path.exists(temp_dir):
                    can_resume = True
        
        # 根据视频类型选择下载方式
        if video_type.upper() == 'M3U8':
            success = download_m3u8(url, output_path, task_id, headers, can_resume)
        else:
            success = download_file_multithread(url, output_path, task_id, headers, num_threads, can_resume)
        
        with download_lock:
            # 再次检查任务状态
            if task_id not in download_tasks:
                return
            
            task_status = download_tasks[task_id].get('status')
            
            # 如果任务被取消或暂停，不更新状态
            if task_status in ['cancelled', 'paused']:
                if task_status == 'cancelled' and os.path.exists(output_path):
                    try:
                        os.remove(output_path)
                    except:
                        pass
                return
            
            if success:
                download_tasks[task_id]['status'] = 'completed'
                download_tasks[task_id]['end_time'] = time.time()
                # 获取文件大小
                if os.path.exists(output_path):
                    download_tasks[task_id]['file_size'] = os.path.getsize(output_path)
                    download_tasks[task_id]['downloaded'] = download_tasks[task_id]['file_size']
                # 删除进度文件
                delete_download_progress(task_id)
            else:
                # 下载失败，但保留进度以便续传
                download_tasks[task_id]['status'] = 'failed'
                download_tasks[task_id]['error'] = '下载失败，可以尝试恢复下载'
    except Exception as e:
        with download_lock:
            if task_id in download_tasks:
                task_status = download_tasks[task_id].get('status')
                # 只有在任务未被取消或暂停时才标记为失败
                if task_status not in ['cancelled', 'paused']:
                    download_tasks[task_id]['status'] = 'failed'
                    download_tasks[task_id]['error'] = str(e)
        print(f"下载任务失败: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 从活动线程列表中移除
        with download_thread_lock:
            if task_id in active_download_threads:
                del active_download_threads[task_id]


@app.route('/api/download', methods=['POST'])
def start_download():
    """开始下载任务"""
    try:
        data = request.get_json()
        
        # 验证必需字段
        if not data.get('url'):
            return jsonify({'error': 'URL不能为空'}), 400
        if not data.get('name'):
            return jsonify({'error': '视频名称不能为空'}), 400
        
        url = data.get('url')
        name = data.get('name')
        video_type = data.get('type', 'VIDEO')
        user_agent = data.get('useragent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        output_format = data.get('outputformat', 'mp4')
        download_dir = data.get('dir', '')
        
        # 生成任务ID
        task_id = generate_task_id(url, name)
        
        # 确定输出文件路径
        if download_dir:
            output_dir = os.path.join(DOWNLOAD_DIR, download_dir)
            os.makedirs(output_dir, exist_ok=True)
        else:
            output_dir = DOWNLOAD_DIR
        
        # 清理文件名（保留中文、英文、数字、空格、连字符、下划线）
        safe_name = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', name).strip()
        # 替换空格为下划线，避免文件名问题
        safe_name = re.sub(r'\s+', '_', safe_name)
        if not safe_name:
            safe_name = 'video'
        output_filename = f"{safe_name}.{output_format}"
        output_path = os.path.join(output_dir, output_filename)
        
        # 如果文件已存在，添加序号
        counter = 1
        while os.path.exists(output_path):
            output_filename = f"{safe_name}_{counter}.{output_format}"
            output_path = os.path.join(output_dir, output_filename)
            counter += 1
        
        # 创建下载任务
        headers = {'User-Agent': user_agent}
        if data.get('referer'):
            headers['Referer'] = data.get('referer')
        
        with download_lock:
            download_tasks[task_id] = {
                'task_id': task_id,
                'url': url,
                'name': name,
                'output_path': output_path,
                'output_filename': output_filename,
                'status': 'pending',
                'video_type': video_type,
                'progress': 0,
                'downloaded': 0,
                'total_size': 0,
                'total_segments': 0,
                'downloaded_segments': 0,
                'start_time': None,
                'end_time': None,
                'error': None,
                'file_size': 0
            }
        
        # 检查并发下载限制
        with download_thread_lock:
            active_count = len([tid for tid, thread in active_download_threads.items() 
                               if thread.is_alive() and download_tasks.get(tid, {}).get('status') == 'downloading'])
            
            if active_count >= MAX_CONCURRENT_DOWNLOADS:
                # 设置为pending状态，等待队列
                with download_lock:
                    download_tasks[task_id]['status'] = 'pending'
                return jsonify({
                    'success': True,
                    'task_id': task_id,
                    'message': f'下载任务已添加到队列（当前有{active_count}个任务正在下载，最多同时{MAX_CONCURRENT_DOWNLOADS}个）'
                })
        
        # 启动下载线程
        thread = threading.Thread(
            target=download_task_worker,
            args=(task_id, url, output_path, video_type, headers, MAX_WORKERS, False)
        )
        thread.daemon = True
        thread.start()
        
        # 添加到活动线程列表
        with download_thread_lock:
            active_download_threads[task_id] = thread
        
        # 启动队列处理线程（如果不存在）
        if not hasattr(start_download, '_queue_thread_started'):
            queue_thread = threading.Thread(target=process_download_queue, daemon=True)
            queue_thread.start()
            start_download._queue_thread_started = True
        
        return jsonify({
            'success': True,
            'task_id': task_id,
            'message': '下载任务已启动'
        })
    
    except Exception as e:
        print(f"启动下载失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'启动下载失败: {str(e)}'}), 500


def process_download_queue():
    """处理下载队列，当有可用槽位时启动pending状态的任务"""
    while True:
        try:
            time.sleep(2)  # 每2秒检查一次
            
            with download_thread_lock:
                active_count = len([tid for tid, thread in active_download_threads.items() 
                                   if thread.is_alive() and download_tasks.get(tid, {}).get('status') == 'downloading'])
            
            if active_count < MAX_CONCURRENT_DOWNLOADS:
                # 查找pending状态的任务
                with download_lock:
                    pending_tasks = [tid for tid, task in download_tasks.items() 
                                   if task.get('status') == 'pending']
                
                if pending_tasks:
                    # 启动第一个pending任务
                    task_id = pending_tasks[0]
                    task = download_tasks[task_id]
                    
                    # 启动下载线程
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                    if task.get('referer'):
                        headers['Referer'] = task['referer']
                    
                    thread = threading.Thread(
                        target=download_task_worker,
                        args=(task_id, task['url'], task['output_path'], 
                              task['video_type'], headers, 
                              MAX_WORKERS, False)
                    )
                    thread.daemon = True
                    thread.start()
                    
                    # 添加到活动线程列表
                    with download_thread_lock:
                        active_download_threads[task_id] = thread
        except Exception as e:
            print(f"处理下载队列失败: {e}")
            time.sleep(5)


@app.route('/api/download/<task_id>', methods=['GET'])
def get_download_status(task_id):
    """获取下载任务状态"""
    with download_lock:
        if task_id not in download_tasks:
            return jsonify({'error': '任务不存在'}), 404
        
        task = download_tasks[task_id].copy()
        
        # 计算进度
        if task['total_size'] > 0:
            task['progress'] = (task['downloaded'] / task['total_size']) * 100
        elif task['total_segments'] > 0:
            task['progress'] = (task['downloaded_segments'] / task['total_segments']) * 100
        else:
            task['progress'] = 0
        
        # 计算速度
        if task['start_time'] and task['status'] == 'downloading':
            elapsed = time.time() - task['start_time']
            if elapsed > 0:
                task['speed'] = task['downloaded'] / elapsed  # bytes per second
            else:
                task['speed'] = 0
        else:
            task['speed'] = 0
        
        # 计算剩余时间
        if task['speed'] > 0 and task['total_size'] > 0:
            remaining = task['total_size'] - task['downloaded']
            task['eta'] = remaining / task['speed']  # seconds
        else:
            task['eta'] = 0
        
        return jsonify(task)


@app.route('/api/download/<task_id>', methods=['DELETE'])
def delete_download(task_id):
    """删除下载任务"""
    try:
        with download_lock:
            if task_id not in download_tasks:
                return jsonify({'error': '任务不存在'}), 404
            
            task = download_tasks[task_id]
            task_status = task.get('status', 'unknown')
            
            # 如果任务正在下载，先标记为取消（实际停止下载需要更复杂的逻辑）
            # 这里允许删除，但会清理资源
            if task_status == 'downloading':
                # 标记任务为已取消（实际下载线程可能会继续运行，但会被清理）
                task['status'] = 'cancelled'
            
            # 删除文件（如果存在）
            output_path = task.get('output_path')
            if output_path and os.path.exists(output_path):
                try:
                    os.remove(output_path)
                    print(f"已删除文件: {output_path}")
                except Exception as e:
                    print(f"删除文件失败: {e}")
            
            # 删除临时目录
            temp_dir = os.path.join(TEMP_DIR, task_id)
            if os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    print(f"已删除临时目录: {temp_dir}")
                except Exception as e:
                    print(f"删除临时目录失败: {e}")
            
            # 从任务列表中删除
            del download_tasks[task_id]
        
        return jsonify({'success': True, 'message': '任务已删除'})
    
    except Exception as e:
        print(f"删除任务失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'删除任务失败: {str(e)}'}), 500


@app.route('/api/downloads', methods=['GET'])
def list_downloads():
    """列出所有下载任务"""
    with download_lock:
        tasks = []
        for task_id, task in download_tasks.items():
            task_copy = task.copy()
            
            # 计算进度
            if task_copy['total_size'] > 0:
                task_copy['progress'] = (task_copy['downloaded'] / task_copy['total_size']) * 100
            elif task_copy['total_segments'] > 0:
                task_copy['progress'] = (task_copy['downloaded_segments'] / task_copy['total_segments']) * 100
            else:
                task_copy['progress'] = 0
            
            tasks.append(task_copy)
        
        # 按创建时间倒序排列
        tasks.sort(key=lambda x: x.get('start_time') or 0, reverse=True)
        
        return jsonify({'tasks': tasks})


@app.route('/api/download/<task_id>/file', methods=['GET'])
def download_file(task_id):
    """下载已完成的文件"""
    with download_lock:
        if task_id not in download_tasks:
            return jsonify({'error': '任务不存在'}), 404
        
        task = download_tasks[task_id]
        
        if task['status'] != 'completed':
            return jsonify({'error': '文件尚未下载完成'}), 400
        
        if not os.path.exists(task['output_path']):
            return jsonify({'error': '文件不存在'}), 404
        
        # 返回文件
        directory = os.path.dirname(task['output_path'])
        filename = os.path.basename(task['output_path'])
        return send_from_directory(directory, filename, as_attachment=True)


@app.route('/api/download/<task_id>/pause', methods=['POST'])
def pause_download(task_id):
    """暂停下载任务"""
    with download_lock:
        if task_id not in download_tasks:
            return jsonify({'error': '任务不存在'}), 404
        
        task = download_tasks[task_id]
        if task['status'] not in ['downloading', 'pending']:
            return jsonify({'error': f'任务状态为{task["status"]}，无法暂停'}), 400
        
        task['status'] = 'paused'
        
        return jsonify({
            'success': True,
            'message': '下载任务已暂停'
        })


@app.route('/api/download/<task_id>/resume', methods=['POST'])
def resume_download(task_id):
    """恢复下载任务（支持断点续传）"""
    with download_lock:
        if task_id not in download_tasks:
            return jsonify({'error': '任务不存在'}), 404
        
        task = download_tasks[task_id]
        if task['status'] != 'paused':
            return jsonify({'error': f'任务状态为{task["status"]}，无法恢复'}), 400
        
        # 检查是否有活动下载线程
        with download_thread_lock:
            has_active_thread = task_id in active_download_threads and active_download_threads[task_id].is_alive()
        
        if not has_active_thread:
            # 检查并发下载限制
            with download_thread_lock:
                active_count = len([tid for tid, thread in active_download_threads.items() 
                                   if thread.is_alive() and download_tasks.get(tid, {}).get('status') == 'downloading'])
                
                if active_count >= MAX_CONCURRENT_DOWNLOADS:
                    task['status'] = 'pending'
                    return jsonify({
                        'success': True,
                        'message': f'下载任务已添加到队列（当前有{active_count}个任务正在下载）'
                    })
            
            # 启动新的下载线程（断点续传）
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            if task.get('referer'):
                headers['Referer'] = task['referer']
            
            thread = threading.Thread(
                target=download_task_worker,
                args=(task_id, task['url'], task['output_path'], 
                      task['video_type'], headers, 
                      MAX_WORKERS, True)  # resume=True
            )
            thread.daemon = True
            thread.start()
            
            with download_thread_lock:
                active_download_threads[task_id] = thread
        else:
            # 线程仍在运行，只需改变状态
            task['status'] = 'downloading'
        
        return jsonify({
            'success': True,
            'message': '下载任务已恢复'
        })


@app.route('/api/download/manual', methods=['POST'])
def manual_add_download():
    """手动添加下载任务"""
    try:
        data = request.get_json()
        
        # 验证必需字段
        if not data.get('url'):
            return jsonify({'error': 'URL不能为空'}), 400
        
        url = data.get('url')
        name = data.get('name', os.path.basename(urlparse(url).path) or 'video')
        video_type = data.get('type', 'VIDEO')  # 默认VIDEO，可以通过URL判断
        user_agent = data.get('useragent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        output_format = data.get('outputformat', 'mp4')
        download_dir = data.get('dir', '')
        
        # 从URL判断视频类型
        if '.m3u8' in url.lower():
            video_type = 'M3U8'
            if not output_format or output_format == 'mp4':
                output_format = 'ts'
        elif any(ext in url.lower() for ext in ['.mp4', '.mkv', '.avi', '.flv', '.webm']):
            video_type = 'VIDEO'
            # 从URL提取文件扩展名
            parsed = urlparse(url)
            path_ext = os.path.splitext(parsed.path)[1]
            if path_ext:
                output_format = path_ext.lstrip('.')
        
        # 生成任务ID
        task_id = generate_task_id(url, name)
        
        # 确定输出文件路径
        if download_dir:
            output_dir = os.path.join(DOWNLOAD_DIR, download_dir)
            os.makedirs(output_dir, exist_ok=True)
        else:
            output_dir = DOWNLOAD_DIR
        
        # 清理文件名
        safe_name = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', name).strip()
        safe_name = re.sub(r'\s+', '_', safe_name)
        if not safe_name:
            safe_name = 'video'
        output_filename = f"{safe_name}.{output_format}"
        output_path = os.path.join(output_dir, output_filename)
        
        # 如果文件已存在，添加序号
        counter = 1
        while os.path.exists(output_path):
            output_filename = f"{safe_name}_{counter}.{output_format}"
            output_path = os.path.join(output_dir, output_filename)
            counter += 1
        
        # 创建下载任务
        headers = {'User-Agent': user_agent}
        if data.get('referer'):
            headers['Referer'] = data.get('referer')
        
        with download_lock:
            download_tasks[task_id] = {
                'task_id': task_id,
                'url': url,
                'name': name,
                'output_path': output_path,
                'output_filename': output_filename,
                'status': 'pending',
                'video_type': video_type,
                'progress': 0,
                'downloaded': 0,
                'total_size': 0,
                'total_segments': 0,
                'downloaded_segments': 0,
                'start_time': None,
                'end_time': None,
                'error': None,
                'file_size': 0,
                'referer': data.get('referer')
            }
        
        # 检查并发下载限制
        with download_thread_lock:
            active_count = len([tid for tid, thread in active_download_threads.items() 
                               if thread.is_alive() and download_tasks.get(tid, {}).get('status') == 'downloading'])
            
            if active_count >= MAX_CONCURRENT_DOWNLOADS:
                return jsonify({
                    'success': True,
                    'task_id': task_id,
                    'message': f'下载任务已添加到队列（当前有{active_count}个任务正在下载，最多同时{MAX_CONCURRENT_DOWNLOADS}个）'
                })
        
        # 启动下载线程
        thread = threading.Thread(
            target=download_task_worker,
            args=(task_id, url, output_path, video_type, headers, MAX_WORKERS, False)
        )
        thread.daemon = True
        thread.start()
        
        # 添加到活动线程列表
        with download_thread_lock:
            active_download_threads[task_id] = thread
        
        # 启动队列处理线程（如果不存在）
        if not hasattr(manual_add_download, '_queue_thread_started'):
            queue_thread = threading.Thread(target=process_download_queue, daemon=True)
            queue_thread.start()
            manual_add_download._queue_thread_started = True
        
        return jsonify({
            'success': True,
            'task_id': task_id,
            'message': '下载任务已启动'
        })
    
    except Exception as e:
        print(f"手动添加下载失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'手动添加下载失败: {str(e)}'}), 500


@app.route('/api/download/<task_id>/update_url', methods=['POST'])
def update_download_url(task_id):
    """更新下载任务的URL（用于失效的下载链接）"""
    try:
        data = request.get_json()
        new_url = data.get('url')
        
        if not new_url:
            return jsonify({'error': '新URL不能为空'}), 400
        
        with download_lock:
            if task_id not in download_tasks:
                return jsonify({'error': '任务不存在'}), 404
            
            task = download_tasks[task_id]
            
            # 只有在失败或暂停状态才能更新URL
            if task['status'] not in ['failed', 'paused', 'cancelled']:
                return jsonify({'error': f'任务状态为{task["status"]}，无法更新URL'}), 400
            
            # 更新URL
            old_url = task['url']
            task['url'] = new_url
            task['error'] = None  # 清除错误信息
            
            # 如果任务处于失败状态，自动恢复下载
            if task['status'] == 'failed':
                task['status'] = 'pending'
            
            # 检查是否有活动下载线程
            with download_thread_lock:
                has_active_thread = task_id in active_download_threads and active_download_threads[task_id].is_alive()
            
            if not has_active_thread and task['status'] == 'pending':
                # 检查并发下载限制
                with download_thread_lock:
                    active_count = len([tid for tid, thread in active_download_threads.items() 
                                       if thread.is_alive() and download_tasks.get(tid, {}).get('status') == 'downloading'])
                    
                    if active_count < MAX_CONCURRENT_DOWNLOADS:
                        # 启动新的下载线程（断点续传）
                        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                        if task.get('referer'):
                            headers['Referer'] = task['referer']
                        
                        thread = threading.Thread(
                            target=download_task_worker,
                            args=(task_id, new_url, task['output_path'], 
                                  task['video_type'], headers, 
                                  MAX_WORKERS, True)  # resume=True
                        )
                        thread.daemon = True
                        thread.start()
                        
                        with download_thread_lock:
                            active_download_threads[task_id] = thread
        
        return jsonify({
            'success': True,
            'message': f'URL已更新，从 {old_url} 更新为 {new_url}'
        })
    
    except Exception as e:
        print(f"更新下载URL失败: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'更新下载URL失败: {str(e)}'}), 500


if __name__ == '__main__':
    # 禁用SSL验证警告
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    app.run(host='0.0.0.0', port=5000, debug=False)
