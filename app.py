from flask import Flask, render_template, request, jsonify, send_from_directory, session, redirect, url_for
from functools import wraps
import requests
import re
import json
import time
import os
import threading
import gc  # 用于垃圾回收，防止内存泄漏
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse, urljoin, parse_qs, unquote
import hashlib
from pathlib import Path
import shutil
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-this-in-production')  # 生产环境请更改此密钥

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

# 用户认证配置（简单单用户登录）
USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')
PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')  # 生产环境请更改默认密码

# 解析队列管理（内存存储，可扩展为数据库）
parse_queue = []  # 存储待解析的URL队列，格式: [{'id': id, 'url': url, 'priority': priority, 'status': status, 'created_at': timestamp}]
parse_queue_lock = threading.Lock()
parse_queue_counter = 0  # 用于生成唯一ID

# 解析历史记录（内存存储，可扩展为数据库）
parse_history = {}  # 格式: {queue_id: {'url': url, 'videos': [...], 'page_title': title, 'parsed_at': timestamp, 'status': 'success'/'failed'}}
parse_history_lock = threading.Lock()

# 自动选择配置
auto_select_prefixes = []  # 存储前缀匹配规则，格式: ['前缀1', '前缀2', ...]
auto_select_lock = threading.Lock()

# 视频名字前缀配置
video_name_prefix = os.environ.get('VIDEO_NAME_PREFIX', '')  # 默认为空

# 解析和下载互斥控制（确保解析和下载不同时执行）
parse_download_mutex_lock = threading.Lock()  # 用于控制解析和下载的互斥执行

# 设置环境变量以确保在无GUI系统中正常运行
if 'DISPLAY' not in os.environ:
    os.environ['DISPLAY'] = ':99'
os.environ['QT_QPA_PLATFORM'] = 'offscreen'


# 浏览器实例管理（防止内存泄漏）
_browser_instances = {}  # 存储浏览器实例
_browser_lock = threading.Lock()
MAX_BROWSER_INSTANCES = int(os.environ.get('MAX_BROWSER_INSTANCES', '2'))  # 限制并发浏览器实例数


def extract_real_m3u8_from_url(url):
    """从URL中提取真实的m3u8地址（如果URL参数中包含m3u8地址）"""
    try:
        from urllib.parse import parse_qs
        parsed_url = urlparse(url)
        query_params = parse_qs(parsed_url.query)
        
        # 常见的m3u8参数名（按优先级排序）
        m3u8_param_names = ['url', 'm3u8', 'src', 'source', 'video', 'stream', 'play', 'file', 'link', 'path', 'v', 'u', 's']
        for param_name in m3u8_param_names:
            if param_name in query_params:
                param_value = query_params[param_name][0] if isinstance(query_params[param_name], list) else query_params[param_name]
                # URL解码（可能需要多次解码）
                for _ in range(3):  # 最多解码3次（处理多重编码）
                    try:
                        decoded = unquote(param_value)
                        if decoded == param_value:
                            break
                        param_value = decoded
                    except:
                        break
                
                # 检查是否是m3u8 URL
                if '.m3u8' in param_value.lower() or 'm3u8' in param_value.lower():
                    if param_value.startswith('http://') or param_value.startswith('https://'):
                        print(f"从URL参数 '{param_name}' 中提取到真实M3U8地址: {param_value[:100]}...")
                        return param_value
                    else:
                        # 相对路径，构建完整URL
                        base_url_parsed = f"{parsed_url.scheme}://{parsed_url.netloc}"
                        real_url = urljoin(base_url_parsed, param_value)
                        print(f"从URL参数 '{param_name}' 中提取到真实M3U8地址（相对路径）: {real_url[:100]}...")
                        return real_url
        
        # 检查URL fragment（#后面的部分）
        if parsed_url.fragment:
            fragment = unquote(parsed_url.fragment)
            if '.m3u8' in fragment.lower():
                # 尝试从fragment中提取URL
                fragment_url_match = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', fragment, re.IGNORECASE)
                if fragment_url_match:
                    real_url = fragment_url_match.group(1)
                    print(f"从URL fragment中提取到真实M3U8地址: {real_url[:100]}...")
                    return real_url
                elif fragment.startswith('http://') or fragment.startswith('https://'):
                    print(f"从URL fragment中提取到真实M3U8地址: {fragment[:100]}...")
                    return fragment
    except Exception as e:
        print(f"提取真实M3U8地址时出错: {e}")
    
    # 如果没有找到，返回原URL
    return url


def recursively_parse_m3u8_with_playwright(page, m3u8_url, max_depth=3, current_depth=0, visited_urls=None, all_urls=None):
    """使用Playwright递归解析m3u8 URL，提取所有嵌套的m3u8地址
    
    解析逻辑：
    1. 先检查URL是否是参数形式，如果是则提取真实地址
    2. 访问URL获取内容
    3. 如果是真正的m3u8文件，检查内容中是否有嵌套的m3u8地址
    4. 如果是HTML，从中提取m3u8地址
    5. 递归处理所有找到的地址
    6. 所有地址都添加到列表供用户选择
    
    Args:
        page: Playwright页面对象
        m3u8_url: 要解析的m3u8 URL
        max_depth: 最大递归深度
        current_depth: 当前递归深度
        visited_urls: 已访问的URL集合
        all_urls: 存储所有找到的URL的列表
    
    Returns:
        找到的所有m3u8 URL列表
    """
    if visited_urls is None:
        visited_urls = set()
    if all_urls is None:
        all_urls = []
    
    # 防止无限递归
    if current_depth >= max_depth:
        print(f"达到最大递归深度 {max_depth}，停止解析: {m3u8_url[:100]}...")
        return all_urls
    
    # 防止循环
    if m3u8_url in visited_urls:
        print(f"检测到循环URL，停止解析: {m3u8_url[:100]}...")
        return all_urls
    
    visited_urls.add(m3u8_url)
    
    try:
        print(f"开始解析M3U8 URL (深度 {current_depth}): {m3u8_url[:100]}...")
        
        # 步骤1: 检查URL是否是参数形式，如果是则提取真实地址
        extracted_url = extract_real_m3u8_from_url(m3u8_url)
        
        # 如果提取到了不同的URL，说明是参数形式，需要递归解析真实地址
        if extracted_url != m3u8_url:
            print(f"从URL参数中提取到真实地址: {extracted_url[:100]}...")
            # 将原始URL添加到列表（用户可能也需要）
            if m3u8_url not in [u['url'] for u in all_urls]:
                all_urls.append({
                    'url': m3u8_url,
                    'type': 'M3U8',
                    'content_type': 'application/vnd.apple.mpegurl',
                    'source': f'param_extracted_{current_depth}',
                    'depth': current_depth
                })
            # 递归解析提取出的真实地址
            recursively_parse_m3u8_with_playwright(
                page, extracted_url, max_depth, current_depth + 1, visited_urls, all_urls
            )
            return all_urls
        
        # 步骤2: 获取m3u8文件内容（优先使用requests，更可靠）
        try:
            # 方法1: 使用requests直接获取（更可靠，适合m3u8文本文件）
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': '*/*',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Referer': m3u8_url
            }
            
            try:
                http_response = requests.get(m3u8_url, headers=headers, timeout=15, verify=False, allow_redirects=True)
                http_response.raise_for_status()
                content = http_response.text
                print(f"使用requests成功获取M3U8内容 ({len(content)} 字节): {m3u8_url[:100]}...")
            except Exception as req_error:
                print(f"使用requests获取失败，尝试使用Playwright: {req_error}")
                # 方法2: 如果requests失败，使用Playwright（可能页面需要JavaScript）
                try:
                    response = page.goto(m3u8_url, wait_until="networkidle", timeout=30000)
                    if not response:
                        print(f"无法访问M3U8 URL: {m3u8_url[:100]}...")
                        if m3u8_url not in [u['url'] for u in all_urls]:
                            all_urls.append({
                                'url': m3u8_url,
                                'type': 'M3U8',
                                'content_type': 'application/vnd.apple.mpegurl',
                                'source': f'failed_{current_depth}',
                                'depth': current_depth
                            })
                        return all_urls
                    
                    # 等待一下，确保JavaScript执行完成
                    time.sleep(1)
                    
                    # 获取响应内容
                    try:
                        content = response.text()
                    except:
                        # 如果无法获取文本，尝试从页面获取
                        try:
                            content = page.content()
                        except:
                            print(f"无法获取M3U8内容: {m3u8_url[:100]}...")
                            if m3u8_url not in [u['url'] for u in all_urls]:
                                all_urls.append({
                                    'url': m3u8_url,
                                    'type': 'M3U8',
                                    'content_type': 'application/vnd.apple.mpegurl',
                                    'source': f'no_content_{current_depth}',
                                    'depth': current_depth
                                })
                            return all_urls
                except Exception as pw_error:
                    print(f"使用Playwright获取也失败: {pw_error}")
                    if m3u8_url not in [u['url'] for u in all_urls]:
                        all_urls.append({
                            'url': m3u8_url,
                            'type': 'M3U8',
                            'content_type': 'application/vnd.apple.mpegurl',
                            'source': f'error_{current_depth}',
                            'depth': current_depth
                        })
                    return all_urls
            
            # 如果内容很小，可能是重定向或需要JavaScript执行，尝试从页面中提取
            if len(content) < 1000:
                print(f"M3U8响应内容很小 ({len(content)} 字节)，可能是HTML或需要JavaScript执行，尝试从页面提取...")
                try:
                    # 尝试执行JavaScript获取页面中的所有m3u8 URL
                    js_m3u8_urls = page.evaluate("""
                        () => {
                            const urls = [];
                            // 查找所有包含m3u8的链接
                            document.querySelectorAll('a[href*="m3u8"], script, meta, link').forEach(el => {
                                const href = el.href || el.src || el.content || '';
                                if (href.includes('m3u8')) {
                                    urls.push(href);
                                }
                            });
                            // 查找页面文本中的m3u8 URL
                            const text = document.body.innerText || document.body.textContent || '';
                            const urlRegex = /https?:\/\/[^\s"']+\.m3u8[^\s"']*/gi;
                            const matches = text.match(urlRegex);
                            if (matches) {
                                urls.push(...matches);
                            }
                            return [...new Set(urls)];
                        }
                    """)
                    
                    if js_m3u8_urls:
                        print(f"通过JavaScript找到 {len(js_m3u8_urls)} 个m3u8 URL")
                        for js_url in js_m3u8_urls:
                            if js_url and js_url not in [u['url'] for u in all_urls]:
                                print(f"从JavaScript提取到M3U8 URL: {js_url[:150]}...")
                                # 递归解析这个URL
                                recursively_parse_m3u8_with_playwright(
                                    page, js_url, max_depth, current_depth + 1, visited_urls, all_urls
                                )
                except Exception as e:
                    print(f"从JavaScript提取m3u8 URL时出错: {e}")
        except Exception as e:
            print(f"访问M3U8 URL时出错: {e}")
            # 即使出错，也添加到列表
            if m3u8_url not in [u['url'] for u in all_urls]:
                all_urls.append({
                    'url': m3u8_url,
                    'type': 'M3U8',
                    'content_type': 'application/vnd.apple.mpegurl',
                    'source': f'error_{current_depth}',
                    'depth': current_depth
                })
            return all_urls
        
        # 步骤3: 检查是否是真正的m3u8文件
        if content.strip().startswith('#EXTM3U'):
            print(f"找到真实的M3U8文件: {m3u8_url[:100]}...")
            
            # 将当前URL添加到列表
            if m3u8_url not in [u['url'] for u in all_urls]:
                all_urls.append({
                    'url': m3u8_url,
                    'type': 'M3U8',
                    'content_type': 'application/vnd.apple.mpegurl',
                    'source': f'm3u8_file_{current_depth}',
                    'depth': current_depth
                })
            
            # 步骤4: 解析m3u8内容，查找嵌套的m3u8地址
            nested_urls = []
            
            # 模式1: #EXT-X-STREAM-INF后面跟着URL（保留完整URL，包括参数）
            stream_inf_pattern = r'#EXT-X-STREAM-INF[^\n]*\n([^\n]+\.m3u8[^\n]*)'
            stream_matches = re.findall(stream_inf_pattern, content, re.IGNORECASE | re.MULTILINE)
            for match in stream_matches:
                match = match.strip()
                if match and match not in nested_urls:
                    nested_urls.append(match)  # 保留完整URL，包括查询参数
            
            # 模式2: 查找所有包含.m3u8的行（排除注释行），保留完整URL
            for line in content.split('\n'):
                line = line.strip()
                if line and not line.startswith('#'):
                    if '.m3u8' in line.lower():
                        # 保留完整URL，包括查询参数和片段（这些参数可能是必需的）
                        # 只清理行首尾的空白字符，保留URL的完整内容
                        clean_line = line.strip()
                        # 移除可能的控制字符和换行符
                        clean_line = clean_line.replace('\r', '').replace('\n', '').replace('\t', ' ')
                        # 如果包含空格，可能是URL后面有其他内容，只取URL部分
                        if ' ' in clean_line:
                            # 尝试提取URL部分（URL通常不包含空格）
                            url_part = clean_line.split()[0]
                            if '.m3u8' in url_part.lower():
                                clean_line = url_part
                        if clean_line and clean_line not in nested_urls:
                            nested_urls.append(clean_line)
            
            # 模式3: 查找URL中的m3u8参数（即使行本身不包含.m3u8）
            # 有些m3u8文件可能包含类似 "url=https://...m3u8" 的格式
            url_param_pattern = r'(?:url|src|source|video|stream|play|file|link|path|m3u8)=([^\s\n]+\.m3u8[^\s\n]*)'
            param_matches = re.findall(url_param_pattern, content, re.IGNORECASE)
            for match in param_matches:
                match = match.strip()
                if match and match not in nested_urls:
                    nested_urls.append(match)
            
            # 模式4: 查找所有完整的http(s)://...m3u8 URL（即使不在单独的行中）
            full_url_pattern = r'https?://[^\s\n"\'<>]+\.m3u8[^\s\n"\'<>]*'
            full_url_matches = re.findall(full_url_pattern, content, re.IGNORECASE)
            for match in full_url_matches:
                match = match.strip()
                if match and match not in nested_urls:
                    nested_urls.append(match)
            
            # 步骤5: 递归处理所有嵌套的m3u8地址
            if nested_urls:
                print(f"在M3U8文件中发现 {len(nested_urls)} 个嵌套的M3U8地址，开始递归解析...")
                print(f"M3U8文件内容预览（前500字符）: {content[:500]}")
                for nested_url_str in nested_urls:
                    print(f"处理嵌套URL: {nested_url_str[:150]}...")
                    
                    # 构建完整URL（保留查询参数和片段）
                    if nested_url_str.startswith('http://') or nested_url_str.startswith('https://'):
                        nested_url = nested_url_str  # 已经是完整URL，直接使用
                    elif nested_url_str.startswith('/') or nested_url_str.startswith('./'):
                        # 绝对路径或相对路径
                        base_url_parsed = f"{urlparse(m3u8_url).scheme}://{urlparse(m3u8_url).netloc}"
                        nested_url = urljoin(base_url_parsed, nested_url_str)
                    else:
                        # 相对路径（不包含/开头，如 "2000k/hls/mixed.m3u8"）
                        # 需要基于当前m3u8 URL的目录来构建
                        base_url_parsed = '/'.join(m3u8_url.split('/')[:-1]) + '/'
                        # 确保base_url以/结尾
                        if not base_url_parsed.endswith('/'):
                            base_url_parsed += '/'
                        nested_url = urljoin(base_url_parsed, nested_url_str)
                        print(f"相对路径转换: {nested_url_str} -> {nested_url[:150]}...")
                    
                    print(f"构建的完整嵌套URL: {nested_url[:150]}...")
                    
                    # 递归解析嵌套地址
                    recursively_parse_m3u8_with_playwright(
                        page, nested_url, max_depth, current_depth + 1, visited_urls, all_urls
                    )
            else:
                # 如果没有找到嵌套地址，但文件很小（可能是主播放列表），尝试更深入的解析
                content_size = len(content)
                print(f"M3U8文件大小: {content_size} 字节")
                # 打印完整内容以便调试
                print(f"M3U8文件完整内容:\n{content}")
                
                # 对于任何大小的m3u8文件，如果没有找到嵌套地址，都尝试深度解析
                # 因为有些m3u8文件可能格式特殊
                if not nested_urls:
                    print(f"未找到嵌套地址，尝试深度解析...")
                    
                    # 尝试查找所有可能的URL模式（更宽泛）
                    all_url_patterns = [
                        r'https?://[^\s\n"\'<>]+\.m3u8[^\s\n"\'<>]*',  # 完整的http(s) m3u8 URL
                        r'/[^\s\n"\'<>]+\.m3u8[^\s\n"\'<>]*',  # 绝对路径m3u8
                        r'\./[^\s\n"\'<>]+\.m3u8[^\s\n"\'<>]*',  # 相对路径m3u8（./开头）
                        r'[a-zA-Z0-9_\-/]+/[^\s\n"\'<>]*\.m3u8[^\s\n"\'<>]*',  # 相对路径（如 2000k/hls/mixed.m3u8）
                        r'[^\s\n"\'<>]+\.m3u8[^\s\n"\'<>]*',  # 任何包含.m3u8的字符串
                    ]
                    
                    for pattern in all_url_patterns:
                        matches = re.findall(pattern, content, re.IGNORECASE)
                        for match in matches:
                            match = match.strip()
                            # 移除可能的控制字符
                            match = match.replace('\r', '').replace('\n', '').replace('\t', ' ')
                            # 如果包含空格，只取第一部分
                            if ' ' in match:
                                match = match.split()[0]
                            if '.m3u8' in match.lower() and match not in nested_urls:
                                print(f"通过深度解析找到可能的m3u8地址: {match[:150]}...")
                                nested_urls.append(match)
                    
                    # 如果找到了新的地址，递归处理
                    if nested_urls:
                        print(f"通过深度解析发现 {len(nested_urls)} 个可能的M3U8地址，开始递归解析...")
                        for nested_url_str in nested_urls:
                            print(f"处理深度解析找到的URL: {nested_url_str[:150]}...")
                            if nested_url_str.startswith('http://') or nested_url_str.startswith('https://'):
                                nested_url = nested_url_str
                            elif nested_url_str.startswith('/') or nested_url_str.startswith('./'):
                                base_url_parsed = f"{urlparse(m3u8_url).scheme}://{urlparse(m3u8_url).netloc}"
                                nested_url = urljoin(base_url_parsed, nested_url_str)
                            else:
                                # 相对路径（如 2000k/hls/mixed.m3u8）
                                base_url_parsed = '/'.join(m3u8_url.split('/')[:-1]) + '/'
                                if not base_url_parsed.endswith('/'):
                                    base_url_parsed += '/'
                                nested_url = urljoin(base_url_parsed, nested_url_str)
                                print(f"相对路径转换: {nested_url_str} -> {nested_url[:150]}...")
                            
                            recursively_parse_m3u8_with_playwright(
                                page, nested_url, max_depth, current_depth + 1, visited_urls, all_urls
                            )
        else:
            # 步骤6: 如果不是m3u8文件，可能是HTML，尝试从中提取
            print(f"M3U8 URL返回的不是M3U8文件，尝试从内容中提取: {m3u8_url[:100]}...")
            
            # 将当前URL添加到列表（即使不是m3u8文件）
            if m3u8_url not in [u['url'] for u in all_urls]:
                all_urls.append({
                    'url': m3u8_url,
                    'type': 'M3U8',
                    'content_type': 'text/html',
                    'source': f'html_{current_depth}',
                    'depth': current_depth
                })
            
            # 从HTML中提取m3u8 URL
            m3u8_patterns = [
                r'(?:url|src|source|video|stream|play|file|link|path|m3u8)["\']?\s*[:=]\s*["\']([^"\']*\.m3u8[^"\']*)["\']',
                r'["\']([^"\']*\.m3u8[^"\']*)["\']',
                r'https?://[^\s"\'<>]+\.m3u8[^\s"\'<>?&]*',
            ]
            
            for pattern in m3u8_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                for match in matches:
                    if isinstance(match, tuple):
                        match = match[0] if match else ''
                    if not match:
                        continue
                    
                    # URL解码
                    for _ in range(3):
                        try:
                            decoded = unquote(match)
                            if decoded == match:
                                break
                            match = decoded
                        except:
                            break
                    
                    # 清理转义字符
                    match = match.replace('\\/', '/').replace('\\"', '"').replace("\\'", "'")
                    
                    if '.m3u8' in match.lower():
                        # 构建完整URL
                        if match.startswith('http://') or match.startswith('https://'):
                            new_url = match
                        elif match.startswith('/') or match.startswith('./'):
                            base_url_parsed = f"{urlparse(m3u8_url).scheme}://{urlparse(m3u8_url).netloc}"
                            new_url = urljoin(base_url_parsed, match)
                        else:
                            base_url_parsed = f"{urlparse(m3u8_url).scheme}://{urlparse(m3u8_url).netloc}"
                            new_url = urljoin(base_url_parsed, '/' + match.lstrip('/'))
                        
                        # 递归解析
                        recursively_parse_m3u8_with_playwright(
                            page, new_url, max_depth, current_depth + 1, visited_urls, all_urls
                        )
                if matches:  # 找到一个就够了，继续递归会找到更多
                    break
        
    except Exception as e:
        print(f"解析M3U8 URL时出错: {e}")
        import traceback
        traceback.print_exc()
        # 即使出错，也添加到列表
        if m3u8_url not in [u['url'] for u in all_urls]:
            all_urls.append({
                'url': m3u8_url,
                'type': 'M3U8',
                'content_type': 'application/vnd.apple.mpegurl',
                'source': f'exception_{current_depth}',
                'depth': current_depth
            })
    
    return all_urls


def extract_video_from_network_requests(url):
    """通过Playwright捕获网络请求来获取所有视频链接（包括m3u8和其他视频格式）
    使用轻量级配置和防内存泄漏措施"""
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
        
        print(f"使用Playwright（轻量级配置）获取视频链接...")
        
        browser = None
        context = None
        page = None
        
        try:
            with sync_playwright() as p:
                # 启动浏览器（headless模式，使用轻量级配置以减少内存占用）
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
                        '--single-process',  # 单进程模式，减少内存占用
                        '--disable-background-networking',  # 禁用后台网络
                        '--disable-background-timer-throttling',  # 禁用后台定时器节流
                        '--disable-backgrounding-occluded-windows',  # 禁用被遮挡窗口的后台处理
                        '--disable-breakpad',  # 禁用崩溃报告
                        '--disable-client-side-phishing-detection',  # 禁用客户端钓鱼检测
                        '--disable-default-apps',  # 禁用默认应用
                        '--disable-features=TranslateUI,BlinkGenPropertyTrees',  # 禁用不需要的功能
                        '--disable-hang-monitor',  # 禁用挂起监控
                        '--disable-ipc-flooding-protection',  # 禁用IPC洪水保护
                        '--disable-popup-blocking',  # 禁用弹窗阻止
                        '--disable-prompt-on-repost',  # 禁用重新提交提示
                        '--disable-renderer-backgrounding',  # 禁用渲染器后台处理
                        '--disable-sync',  # 禁用同步
                        '--disable-translate',  # 禁用翻译
                        '--metrics-recording-only',  # 仅记录指标
                        '--no-default-browser-check',  # 不检查默认浏览器
                        '--no-pings',  # 禁用ping
                        '--safebrowsing-disable-auto-update',  # 禁用安全浏览自动更新
                        '--enable-automation',  # 启用自动化
                        '--password-store=basic',  # 使用基本密码存储
                        '--use-mock-keychain',  # 使用模拟密钥链
                        '--memory-pressure-off',  # 关闭内存压力检测
                        '--max_old_space_size=256',  # 限制V8内存使用（MB）
                        '--disable-web-security',  # 禁用Web安全（仅用于自动化）
                        '--disable-features=IsolateOrigins,site-per-process',  # 禁用站点隔离
                    ]
                )
                
                # 创建浏览器上下文（使用轻量级配置）
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    viewport={'width': 1280, 'height': 720},  # 减小视口大小以节省内存
                    ignore_https_errors=True,
                    java_script_enabled=True,
                    # 禁用不必要的功能
                    bypass_csp=True,
                    # 不加载图片和字体以节省内存
                    no_viewport=False,
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
                                # 对于m3u8 URL，先添加到捕获列表
                                captured_urls[request_url] = {
                                    'type': video_type,
                                    'content_type': None,  # 请求时还没有content-type
                                    'source': 'request'
                                }
                                print(f"从请求中捕获M3U8 URL: {request_url[:100]}...")
                                
                                # 使用Playwright递归解析，提取所有嵌套的m3u8地址
                                try:
                                    nested_urls = recursively_parse_m3u8_with_playwright(page, request_url)
                                    for nested_info in nested_urls:
                                        nested_url = nested_info['url']
                                        if nested_url not in captured_urls:
                                            captured_urls[nested_url] = {
                                                'type': 'M3U8',
                                                'content_type': nested_info.get('content_type', 'application/vnd.apple.mpegurl'),
                                                'source': f"nested_{nested_info.get('depth', 0)}"
                                            }
                                            print(f"发现嵌套的M3U8地址 (深度 {nested_info.get('depth', 0)}): {nested_url[:100]}...")
                                except Exception as e:
                                    print(f"递归解析M3U8 URL时出错: {e}")
                                    import traceback
                                    traceback.print_exc()
                            elif ext == '.ts':
                                video_type = 'TS'
                                captured_urls[request_url] = {
                                    'type': video_type,
                                    'content_type': None,  # 请求时还没有content-type
                                    'source': 'request'
                                }
                                print(f"从请求中捕获视频URL: {request_url[:100]}... (类型: {video_type})")
                            else:
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
                                    # 对于m3u8 URL，先添加到捕获列表
                                    if response_url not in captured_urls:
                                        captured_urls[response_url] = {
                                            'type': video_type,
                                            'content_type': content_type,
                                            'source': 'response'
                                        }
                                        print(f"从响应中捕获M3U8 URL: {response_url[:100]}...")
                                    
                                    # 使用Playwright递归解析，提取所有嵌套的m3u8地址
                                    try:
                                        nested_urls = recursively_parse_m3u8_with_playwright(page, response_url)
                                        for nested_info in nested_urls:
                                            nested_url = nested_info['url']
                                            if nested_url not in captured_urls:
                                                captured_urls[nested_url] = {
                                                    'type': 'M3U8',
                                                    'content_type': nested_info.get('content_type', 'application/vnd.apple.mpegurl'),
                                                    'source': f"nested_{nested_info.get('depth', 0)}"
                                                }
                                                print(f"发现嵌套的M3U8地址 (深度 {nested_info.get('depth', 0)}): {nested_url[:100]}...")
                                    except Exception as e:
                                        print(f"递归解析M3U8 URL时出错: {e}")
                                        import traceback
                                        traceback.print_exc()
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
                                        # 对于m3u8 URL，先添加到捕获列表
                                        if response_url not in captured_urls:
                                            captured_urls[response_url] = {
                                                'type': video_type,
                                                'content_type': content_type,
                                                'source': 'response'
                                            }
                                            print(f"从响应中捕获M3U8 URL: {response_url[:100]}...")
                                        
                                        # 使用Playwright递归解析，提取所有嵌套的m3u8地址
                                        try:
                                            nested_urls = recursively_parse_m3u8_with_playwright(page, response_url)
                                            for nested_info in nested_urls:
                                                nested_url = nested_info['url']
                                                if nested_url not in captured_urls:
                                                    captured_urls[nested_url] = {
                                                        'type': 'M3U8',
                                                        'content_type': nested_info.get('content_type', 'application/vnd.apple.mpegurl'),
                                                        'source': f"nested_{nested_info.get('depth', 0)}"
                                                    }
                                                    print(f"发现嵌套的M3U8地址 (深度 {nested_info.get('depth', 0)}): {nested_url[:100]}...")
                                        except Exception as e:
                                            print(f"递归解析M3U8 URL时出错: {e}")
                                            import traceback
                                            traceback.print_exc()
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
                
                # 注册请求和响应处理器（必须在with块内，在页面关闭之前）
                page.on("request", handle_request)
                page.on("response", handle_response)
            
                # 过滤资源以加快加载并减少内存占用（只加载必要的资源）
                def handle_route(route):
                    """过滤资源，只加载必要的资源，减少内存占用"""
                    resource_type = route.request.resource_type
                    request_url = route.request.url.lower()
                    
                    # 允许文档、脚本、样式表、xhr、fetch、websocket、media（视频、音频）
                    if resource_type in ['document', 'script', 'stylesheet', 'xhr', 'fetch', 'websocket', 'media']:
                        route.continue_()
                    # 阻止图片、字体等（减少内存占用）
                    elif resource_type in ['image', 'font', 'texttrack', 'manifest', 'other']:
                        route.abort()
                    # 阻止大型资源文件
                    elif any(ext in request_url for ext in ['.woff', '.woff2', '.ttf', '.eot', '.otf', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico']):
                        route.abort()
                    else:
                        route.continue_()
                
                try:
                    page.route("**/*", handle_route)
                    print("已启用资源过滤以加快加载并减少内存占用")
                except Exception as e:
                    print(f"资源过滤启用失败: {e}，继续执行...")
                
                try:
                    # 访问页面 - 使用更实用的等待策略，设置超时防止长时间占用资源
                    print(f"正在访问页面: {url}")
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)  # 减少超时时间
                    print("页面DOM加载完成")
                    
                    # 等待JavaScript执行（减少等待时间）
                    time.sleep(2)  # 从3秒减少到2秒
                    
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
                    
                    # 等待网络请求（减少等待时间）
                    print("等待网络请求...")
                    time.sleep(6)  # 从8秒减少到6秒
                    
                    # 获取页面标题
                    try:
                        page_title = page.title()
                        print(f"获取到页面标题: {page_title}")
                    except Exception as e:
                        print(f"获取页面标题失败: {e}")
                    
                except Exception as e:
                    print(f"访问页面失败: {e}")
                finally:
                    # 确保资源正确清理，防止内存泄漏（在with块内清理）
                    try:
                        if page:
                            page.close()
                            page = None
                    except Exception as e:
                        print(f"关闭页面时出错: {e}")
                    
                    try:
                        if context:
                            context.close()
                            context = None
                    except Exception as e:
                        print(f"关闭上下文时出错: {e}")
                    
                    try:
                        if browser:
                            browser.close()
                            browser = None
                    except Exception as e:
                        print(f"关闭浏览器时出错: {e}")
                    
                    # 强制垃圾回收（可选，帮助释放内存）
                    gc.collect()
                
                # 将捕获的URL转换为列表格式（在with块内）
                for url_item, info in captured_urls.items():
                    video_urls.append({
                        'url': url_item,
                        'type': info['type'],
                        'content_type': info.get('content_type', ''),
                        'source': info['source']
                    })
                
                print(f"共捕获到 {len(video_urls)} 个视频链接")
        except Exception as e:
            print(f"浏览器操作失败: {e}")
            import traceback
            traceback.print_exc()
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
        
        # 应用视频名字前缀（如果已配置）
        global video_name_prefix
        if video_name_prefix:
            name = f"{video_name_prefix}{name}"
        
        formatted_links.append({
            'url': url_item,
            'name': name,
            'type': video_type,
            'content_type': content_type
        })
    
    return formatted_links, page_title


# 登录验证装饰器
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session or not session['logged_in']:
            if request.is_json:
                return jsonify({'error': '需要登录'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


@app.route('/login', methods=['GET', 'POST'])
def login():
    """登录页面"""
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        username = data.get('username', '')
        password = data.get('password', '')
        
        if username == USERNAME and password == PASSWORD:
            session['logged_in'] = True
            session['username'] = username
            if request.is_json:
                return jsonify({'success': True, 'message': '登录成功'})
            return redirect(url_for('index'))
        else:
            if request.is_json:
                return jsonify({'error': '用户名或密码错误'}), 401
            return render_template('login.html', error='用户名或密码错误')
    
    # 如果已经登录，重定向到主页
    if session.get('logged_in'):
        return redirect(url_for('index'))
    
    return render_template('login.html')


@app.route('/logout', methods=['POST'])
def logout():
    """登出"""
    session.clear()
    if request.is_json:
        return jsonify({'success': True, 'message': '已登出'})
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    """主页"""
    return render_template('index.html')


@app.route('/downloads')
@login_required
def downloads_page():
    """下载管理页面"""
    return render_template('downloads.html')


@app.route('/parse-queue')
@login_required
def parse_queue_page():
    """解析队列管理页面"""
    return render_template('parse_queue.html')


def _has_active_downloads():
    """检查是否有正在下载的任务"""
    with download_thread_lock:
        active_downloads = [
            tid for tid, thread in active_download_threads.items()
            if thread.is_alive() and download_tasks.get(tid, {}).get('status') == 'downloading'
        ]
        return len(active_downloads) > 0


def _has_active_parse_tasks():
    """检查是否有正在处理或待处理的解析任务"""
    with parse_queue_lock:
        active_tasks = [
            task for task in parse_queue
            if task.get('status') in ['pending', 'processing']
        ]
        return len(active_tasks) > 0


def process_download_queue():
    """处理下载队列，当有可用槽位时启动pending状态的任务（与解析任务互斥）"""
    while True:
        try:
            time.sleep(2)  # 每2秒检查一次
            
            # 检查是否有正在处理的解析任务，如果有则等待
            if _has_active_parse_tasks():
                continue
            
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
                    
                    # 更新任务状态为downloading
                    with download_lock:
                        if task_id in download_tasks:
                            download_tasks[task_id]['status'] = 'downloading'
                            download_tasks[task_id]['start_time'] = time.time()
                    
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
                    
                    print(f"从队列启动下载任务: {task.get('name', task_id)} ({task_id})")
        except Exception as e:
            print(f"处理下载队列失败: {e}")
            time.sleep(5)


def _start_auto_selected_downloads():
    """启动所有自动选择的下载任务（一次性启动，受并发限制控制）"""
    try:
        # 下载队列处理线程已在启动时运行，无需再次启动
        
        # 再次确认所有解析任务已完成
        if _has_active_parse_tasks():
            print("仍有解析任务在处理，延迟启动下载...")
            return
        
        # 查找所有自动选择的pending下载任务
        with download_lock:
            auto_selected_tasks = [
                (task_id, task) for task_id, task in download_tasks.items()
                if task.get('status') == 'pending' and task.get('auto_selected', False)
            ]
        
        if not auto_selected_tasks:
            print("没有待启动的自动选择下载任务")
            return
        
        print(f"解析队列为空，找到 {len(auto_selected_tasks)} 个自动选择的下载任务，开始一次性启动...")
        
        # 一次性启动所有下载任务（受并发限制控制，超出限制的会进入队列）
        started_count = 0
        for task_id, task in auto_selected_tasks:
            try:
                with download_thread_lock:
                    active_count = len([tid for tid, thread in active_download_threads.items() 
                                       if thread.is_alive() and download_tasks.get(tid, {}).get('status') == 'downloading'])
                    
                    if active_count >= MAX_CONCURRENT_DOWNLOADS:
                        # 达到并发限制，剩余任务会由process_download_queue处理
                        print(f"已达到并发下载限制（{active_count}/{MAX_CONCURRENT_DOWNLOADS}），剩余 {len(auto_selected_tasks) - started_count} 个任务将进入队列等待")
                        break
                    
                    # 更新任务状态为downloading
                    with download_lock:
                        if task_id in download_tasks:
                            download_tasks[task_id]['status'] = 'downloading'
                            download_tasks[task_id]['start_time'] = time.time()
                    
                    # 准备请求头
                    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
                    if task.get('referer'):
                        headers['Referer'] = task['referer']
                    
                    # 启动下载线程
                    thread = threading.Thread(
                        target=download_task_worker,
                        args=(task_id, task['url'], task['output_path'], 
                              task['video_type'], headers, MAX_WORKERS, False)
                    )
                    thread.daemon = True
                    thread.start()
                    
                    # 添加到活动线程列表
                    active_download_threads[task_id] = thread
                    started_count += 1
                    print(f"已启动下载任务: {task.get('name', task_id)} ({task_id})")
            except Exception as e:
                print(f"启动下载任务失败 ({task.get('name', task_id)}): {e}")
                import traceback
                traceback.print_exc()
        
        print(f"已启动 {started_count} 个下载任务，剩余 {len(auto_selected_tasks) - started_count} 个任务在队列中等待")
    except Exception as e:
        print(f"启动自动选择下载任务失败: {e}")
        import traceback
        traceback.print_exc()


def process_parse_queue():
    """处理解析队列的线程函数（与下载任务互斥）"""
    while True:
        try:
            time.sleep(2)  # 每2秒检查一次
            
            # 检查是否有正在下载的任务，如果有则等待
            if _has_active_downloads():
                continue
            
            # 查找待解析的任务（按优先级排序）
            with parse_queue_lock:
                pending_tasks = [task for task in parse_queue if task.get('status') == 'pending']
                if not pending_tasks:
                    continue
                
                # 按优先级排序（数字越小优先级越高）
                pending_tasks.sort(key=lambda x: x.get('priority', 999))
                task = pending_tasks[0]
                
                # 标记为处理中
                task['status'] = 'processing'
                task['started_at'] = time.time()
                queue_id = task['id']
                url = task['url']
            
            # 解析URL
            try:
                formatted_links, page_title = extract_video_from_network_requests(url)
                
                # 自动选择（基于URL前缀匹配）
                selected_videos = []
                with auto_select_lock:
                    prefixes = list(auto_select_prefixes)
                
                if prefixes:
                    for link in formatted_links:
                        for prefix in prefixes:
                            # 只根据URL前缀匹配，不根据文件名
                            if link['url'].startswith(prefix):
                                selected_videos.append(link)
                                break
                
                # 保存到历史记录
                with parse_history_lock:
                    parse_history[queue_id] = {
                        'url': url,
                        'videos': formatted_links,
                        'selected_videos': selected_videos,
                        'page_title': page_title,
                        'parsed_at': time.time(),
                        'status': 'success'
                    }
                
                # 为自动选择的视频创建下载任务（但不立即启动，等所有解析完成后再启动）
                if selected_videos:
                    print(f"自动选择到 {len(selected_videos)} 个视频，创建下载任务（等待解析队列完成后启动）...")
                    for video in selected_videos:
                        try:
                            # 创建下载任务
                            video_url = video['url']
                            video_name = video['name']
                            video_type = video.get('type', 'VIDEO')
                            
                            # 生成任务ID
                            task_id = generate_task_id(video_url, video_name)
                            
                            # 确定输出文件路径
                            output_dir = DOWNLOAD_DIR
                            
                            # 清理文件名
                            safe_name = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', video_name).strip()
                            safe_name = re.sub(r'\s+', '_', safe_name)
                            if not safe_name:
                                safe_name = 'video'
                            
                            # 根据视频类型确定输出格式
                            output_format = 'mp4'
                            if video_type == 'M3U8':
                                output_format = 'mp4'
                            elif video_type in ['MP4', 'MOV']:
                                output_format = 'mp4'
                            elif video_type == 'WEBM':
                                output_format = 'webm'
                            
                            output_filename = f"{safe_name}.{output_format}"
                            output_path = os.path.join(output_dir, output_filename)
                            
                            # 如果文件已存在，添加序号
                            counter = 1
                            while os.path.exists(output_path):
                                output_filename = f"{safe_name}_{counter}.{output_format}"
                                output_path = os.path.join(output_dir, output_filename)
                                counter += 1
                            
                            # 创建下载任务（状态为pending，不立即启动）
                            with download_lock:
                                # 检查任务是否已存在（避免重复创建）
                                if task_id not in download_tasks:
                                    download_tasks[task_id] = {
                                        'task_id': task_id,
                                        'url': video_url,
                                        'name': video_name,
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
                                        'referer': url,
                                        'auto_selected': True  # 标记为自动选择的任务
                                    }
                                    print(f"已创建下载任务（待启动）: {video_name} ({task_id})")
                                else:
                                    print(f"下载任务已存在，跳过: {video_name} ({task_id})")
                        except Exception as e:
                            print(f"创建下载任务失败 ({video.get('name', '未知')}): {e}")
                            import traceback
                            traceback.print_exc()
                
                # 更新队列状态
                with parse_queue_lock:
                    for t in parse_queue:
                        if t['id'] == queue_id:
                            t['status'] = 'completed'
                            t['completed_at'] = time.time()
                            break
                
                print(f"解析队列任务 {queue_id} 完成: {url}")
                if selected_videos:
                    print(f"已为 {len(selected_videos)} 个视频创建下载任务（等待解析队列完成后启动）")
                
                # 检查是否所有解析任务都已完成，如果是则启动下载
                if not _has_active_parse_tasks():
                    # 所有解析任务都已完成，启动自动选择的下载任务
                    print("所有解析任务已完成，开始启动自动选择的下载任务...")
                    _start_auto_selected_downloads()
                
            except Exception as e:
                print(f"解析队列任务 {queue_id} 失败: {e}")
                import traceback
                traceback.print_exc()
                
                # 保存失败记录
                with parse_history_lock:
                    parse_history[queue_id] = {
                        'url': url,
                        'videos': [],
                        'selected_videos': [],
                        'page_title': '',
                        'parsed_at': time.time(),
                        'status': 'failed',
                        'error': str(e)
                    }
                
                # 更新队列状态
                with parse_queue_lock:
                    for t in parse_queue:
                        if t['id'] == queue_id:
                            t['status'] = 'failed'
                            t['completed_at'] = time.time()
                            t['error'] = str(e)
                            break
                
                # 即使失败，也要检查是否所有解析任务都已完成（包括失败的任务）
                # 如果所有任务都已完成（completed或failed），可以启动下载
                if not _has_active_parse_tasks():
                    print("所有解析任务已完成（包含失败的任务），检查是否启动下载...")
                    _start_auto_selected_downloads()
                
        except Exception as e:
            print(f"处理解析队列时出错: {e}")
            time.sleep(5)


# 启动解析队列处理线程
parse_queue_thread = threading.Thread(target=process_parse_queue, daemon=True)
parse_queue_thread.start()

# 启动下载队列处理线程（持续运行，与解析任务互斥）
download_queue_thread = threading.Thread(target=process_download_queue, daemon=True)
download_queue_thread.start()
print("解析队列和下载队列处理线程已启动（互斥执行）")


@app.route('/api/parse', methods=['POST'])
@login_required
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
                'suggestion': '视频链接可能通过JavaScript动态加载，请确保已安装playwright。如果内存不足，请检查浏览器配置。'
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


# 解析队列管理API
@app.route('/api/parse-queue', methods=['GET'])
@login_required
def get_parse_queue():
    """获取解析队列"""
    with parse_queue_lock:
        queue_list = list(parse_queue)
    return jsonify({'success': True, 'queue': queue_list})


@app.route('/api/parse-queue', methods=['POST'])
@login_required
def add_parse_queue():
    """添加解析队列任务"""
    try:
        data = request.get_json()
        url = data.get('url', '').strip()
        priority = data.get('priority', 999)  # 默认优先级
        
        if not url:
            return jsonify({'error': 'URL不能为空'}), 400
        
        # 添加协议如果缺失
        if not url.startswith(('http://', 'https://')):
            url = 'https://' + url
        
        global parse_queue_counter
        with parse_queue_lock:
            parse_queue_counter += 1
            queue_id = parse_queue_counter
            task = {
                'id': queue_id,
                'url': url,
                'priority': priority,
                'status': 'pending',
                'created_at': time.time()
            }
            parse_queue.append(task)
            # 按优先级排序
            parse_queue.sort(key=lambda x: x.get('priority', 999))
        
        return jsonify({
            'success': True,
            'queue_id': queue_id,
            'message': '已添加到解析队列'
        })
    except Exception as e:
        return jsonify({'error': f'添加失败: {str(e)}'}), 500


@app.route('/api/parse-queue/<int:queue_id>', methods=['DELETE'])
@login_required
def delete_parse_queue(queue_id):
    """删除解析队列任务"""
    try:
        with parse_queue_lock:
            parse_queue[:] = [task for task in parse_queue if task['id'] != queue_id]
        return jsonify({'success': True, 'message': '已删除'})
    except Exception as e:
        return jsonify({'error': f'删除失败: {str(e)}'}), 500


@app.route('/api/parse-queue/<int:queue_id>/priority', methods=['POST'])
@login_required
def update_parse_queue_priority(queue_id):
    """更新解析队列任务优先级"""
    try:
        data = request.get_json()
        priority = data.get('priority', 999)
        
        with parse_queue_lock:
            for task in parse_queue:
                if task['id'] == queue_id:
                    task['priority'] = priority
                    # 重新排序
                    parse_queue.sort(key=lambda x: x.get('priority', 999))
                    return jsonify({'success': True, 'message': '优先级已更新'})
        
        return jsonify({'error': '任务不存在'}), 404
    except Exception as e:
        return jsonify({'error': f'更新失败: {str(e)}'}), 500


@app.route('/api/parse-history', methods=['GET'])
@login_required
def get_parse_history():
    """获取解析历史记录"""
    with parse_history_lock:
        history_list = []
        for queue_id, record in parse_history.items():
            history_list.append({
                'queue_id': queue_id,
                **record
            })
        # 按时间倒序排列
        history_list.sort(key=lambda x: x.get('parsed_at', 0), reverse=True)
    return jsonify({'success': True, 'history': history_list})


@app.route('/api/parse-history/<int:queue_id>', methods=['GET'])
@login_required
def get_parse_history_detail(queue_id):
    """获取解析历史记录详情"""
    with parse_history_lock:
        if queue_id not in parse_history:
            return jsonify({'error': '记录不存在'}), 404
        return jsonify({'success': True, 'record': parse_history[queue_id]})


@app.route('/api/parse-history/<int:queue_id>', methods=['DELETE'])
@login_required
def delete_parse_history(queue_id):
    """删除解析历史记录"""
    try:
        with parse_history_lock:
            if queue_id in parse_history:
                del parse_history[queue_id]
        return jsonify({'success': True, 'message': '已删除'})
    except Exception as e:
        return jsonify({'error': f'删除失败: {str(e)}'}), 500


@app.route('/api/auto-select', methods=['GET'])
@login_required
def get_auto_select_prefixes():
    """获取自动选择前缀列表"""
    with auto_select_lock:
        return jsonify({'success': True, 'prefixes': list(auto_select_prefixes)})


@app.route('/api/auto-select', methods=['POST'])
@login_required
def set_auto_select_prefixes():
    """设置自动选择前缀列表"""
    try:
        data = request.get_json()
        prefixes = data.get('prefixes', [])
        
        if not isinstance(prefixes, list):
            return jsonify({'error': '前缀列表必须是数组'}), 400
        
        with auto_select_lock:
            auto_select_prefixes[:] = prefixes
        
        return jsonify({'success': True, 'message': '前缀列表已更新'})
    except Exception as e:
        return jsonify({'error': f'设置失败: {str(e)}'}), 500


@app.route('/api/video-name-prefix', methods=['GET'])
@login_required
def get_video_name_prefix():
    """获取视频名字前缀"""
    global video_name_prefix
    return jsonify({'success': True, 'prefix': video_name_prefix})


@app.route('/api/video-name-prefix', methods=['POST'])
@login_required
def set_video_name_prefix():
    """设置视频名字前缀"""
    try:
        data = request.get_json()
        prefix = data.get('prefix', '')
        
        global video_name_prefix
        video_name_prefix = prefix
        
        return jsonify({'success': True, 'message': '前缀已更新'})
    except Exception as e:
        return jsonify({'error': f'设置失败: {str(e)}'}), 500


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
        
        # 下载队列处理线程已在启动时运行，无需再次启动
        
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
@login_required
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
        
        # 下载队列处理线程已在启动时运行，无需再次启动
        
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
@login_required
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
