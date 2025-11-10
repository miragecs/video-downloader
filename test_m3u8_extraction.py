#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
M3U8提取测试程序
实际测试从视频网页中提取m3u8链接的功能
使用方法：
1. 确保app.py正在运行（python app.py 或 docker run）
2. 运行此测试程序：python test_m3u8_extraction.py
3. 输入视频网页URL进行测试
"""

import requests
import json
import sys
from datetime import datetime
import urllib3

# 禁用SSL警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 测试服务器地址
BASE_URL = "http://localhost:5000"

# 颜色输出
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_header(text):
    """打印标题"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.RESET}\n")

def print_success(message):
    """打印成功消息"""
    print(f"{Colors.GREEN}✓ {message}{Colors.RESET}")

def print_error(message):
    """打印错误消息"""
    print(f"{Colors.RED}✗ {message}{Colors.RESET}")

def print_info(message):
    """打印信息消息"""
    print(f"{Colors.YELLOW}ℹ {message}{Colors.RESET}")

def print_m3u8_info(m3u8_item, index):
    """打印m3u8链接信息"""
    print(f"\n{Colors.CYAN}【M3U8 #{index}】{Colors.RESET}")
    print(f"  名称: {m3u8_item.get('name', '未知')}")
    print(f"  URL: {m3u8_item.get('url', '')}")

def check_server():
    """检查服务器是否运行"""
    try:
        response = requests.get(BASE_URL, timeout=5)
        if response.status_code == 200:
            return True
        else:
            return False
    except:
        return False

def test_parse_url(url):
    """测试解析URL并提取m3u8链接"""
    print_header(f"测试URL: {url}")
    
    try:
        # 发送请求到API
        print_info("正在发送请求到API...")
        response = requests.post(
            f"{BASE_URL}/api/parse",
            json={"url": url},
            headers={"Content-Type": "application/json"},
            timeout=60,  # 增加超时时间，因为解析可能需要较长时间
            verify=False
        )
        
        # 检查响应状态
        if response.status_code == 200:
            data = response.json()
            
            if data.get('success'):
                print_success("成功获取到响应！")
                
                # 显示页面标题
                page_title = data.get('page_title', '未知')
                print_info(f"页面标题: {page_title}")
                
                # 显示m3u8链接列表
                m3u8_list = data.get('m3u8_list', [])
                if m3u8_list:
                    print_success(f"找到 {len(m3u8_list)} 个M3U8链接:")
                    for idx, m3u8_item in enumerate(m3u8_list, 1):
                        print_m3u8_info(m3u8_item, idx)
                    
                    # 验证m3u8链接是否可访问
                    print(f"\n{Colors.BOLD}验证M3U8链接可访问性:{Colors.RESET}")
                    for idx, m3u8_item in enumerate(m3u8_list, 1):
                        m3u8_url = m3u8_item.get('url', '')
                        print_info(f"验证 M3U8 #{idx}: {m3u8_url}")
                        try:
                            # 只检查HEAD请求，不下载整个文件
                            verify_response = requests.head(
                                m3u8_url, 
                                timeout=10,
                                allow_redirects=True,
                                verify=False
                            )
                            if verify_response.status_code == 200:
                                print_success(f"  M3U8 #{idx} 链接可访问")
                                # 显示Content-Type
                                content_type = verify_response.headers.get('Content-Type', '')
                                if content_type:
                                    print_info(f"  内容类型: {content_type}")
                            else:
                                print_error(f"  M3U8 #{idx} 链接返回状态码: {verify_response.status_code}")
                        except requests.exceptions.Timeout:
                            print_error(f"  M3U8 #{idx} 链接访问超时")
                        except requests.exceptions.RequestException as e:
                            print_error(f"  M3U8 #{idx} 链接访问失败: {str(e)}")
                    
                    return True, m3u8_list
                else:
                    print_error("未找到M3U8链接")
                    return False, []
            else:
                error_msg = data.get('error', '未知错误')
                print_error(f"API返回错误: {error_msg}")
                return False, []
        
        elif response.status_code == 404:
            data = response.json()
            error_msg = data.get('error', '未找到m3u8链接')
            page_title = data.get('title', '未知')
            print_error(f"未找到M3U8链接: {error_msg}")
            print_info(f"页面标题: {page_title}")
            print_info("可能的原因:")
            print_info("  1. 该网页不包含M3U8格式的视频")
            print_info("  2. M3U8链接是动态加载的（需要JavaScript执行）")
            print_info("  3. 需要登录或特殊权限才能访问")
            print_info("  4. 视频使用其他格式（如MP4、FLV等）")
            return False, []
        
        elif response.status_code == 400:
            data = response.json()
            error_msg = data.get('error', '请求参数错误')
            print_error(f"请求错误: {error_msg}")
            return False, []
        
        elif response.status_code == 500:
            data = response.json()
            error_msg = data.get('error', '服务器内部错误')
            print_error(f"服务器错误: {error_msg}")
            return False, []
        
        else:
            print_error(f"意外的状态码: {response.status_code}")
            print_info(f"响应内容: {response.text[:500]}")
            return False, []
    
    except requests.exceptions.Timeout:
        print_error("请求超时，请检查网络连接或URL是否可访问")
        return False, []
    
    except requests.exceptions.ConnectionError:
        print_error("无法连接到服务器，请确保app.py正在运行")
        print_info(f"尝试访问: {BASE_URL}")
        return False, []
    
    except Exception as e:
        print_error(f"测试失败: {str(e)}")
        import traceback
        print_info(f"详细错误信息:\n{traceback.format_exc()}")
        return False, []

def interactive_test():
    """交互式测试"""
    print_header("M3U8提取功能测试程序")
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"API地址: {BASE_URL}")
    
    # 检查服务器
    print_info("检查服务器连接...")
    if not check_server():
        print_error("无法连接到服务器！")
        print_info("请确保app.py正在运行:")
        print_info("  python app.py")
        print_info("或如果使用Docker:")
        print_info("  docker-compose up")
        return
    print_success("服务器连接正常")
    
    # 测试统计
    test_results = []
    
    while True:
        print(f"\n{Colors.BOLD}{Colors.CYAN}{'-'*70}{Colors.RESET}")
        print(f"{Colors.BOLD}请输入要测试的视频网页URL（输入 'quit' 或 'exit' 退出）:{Colors.RESET}")
        url = input().strip()
        
        if url.lower() in ['quit', 'exit', 'q']:
            break
        
        if not url:
            print_error("URL不能为空，请重新输入")
            continue
        
        # 执行测试
        success, m3u8_list = test_parse_url(url)
        test_results.append({
            'url': url,
            'success': success,
            'm3u8_count': len(m3u8_list) if m3u8_list else 0
        })
        
        # 询问是否继续
        print(f"\n{Colors.YELLOW}是否继续测试其他URL? (y/n): {Colors.RESET}", end='')
        continue_test = input().strip().lower()
        if continue_test not in ['y', 'yes', '']:
            break
    
    # 显示测试总结
    if test_results:
        print_header("测试总结")
        total = len(test_results)
        success_count = sum(1 for r in test_results if r['success'])
        
        for idx, result in enumerate(test_results, 1):
            status = f"{Colors.GREEN}✓ 成功{Colors.RESET}" if result['success'] else f"{Colors.RED}✗ 失败{Colors.RESET}"
            print(f"{idx}. {status} - {result['url']}")
            if result['success']:
                print(f"   找到 {result['m3u8_count']} 个M3U8链接")
        
        print(f"\n总计: {success_count}/{total} 个测试成功")
        
        # 保存测试结果到文件
        try:
            result_file = f"test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(result_file, 'w', encoding='utf-8') as f:
                json.dump(test_results, f, ensure_ascii=False, indent=2)
            print_info(f"测试结果已保存到: {result_file}")
        except Exception as e:
            print_error(f"保存测试结果失败: {str(e)}")

def single_test(url):
    """单次测试（命令行参数）"""
    if not check_server():
        print_error("无法连接到服务器！")
        print_info("请确保app.py正在运行")
        sys.exit(1)
    
    success, m3u8_list = test_parse_url(url)
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    # 如果提供了URL作为命令行参数，执行单次测试
    if len(sys.argv) > 1:
        if sys.argv[1] in ['-h', '--help']:
            print("用法:")
            print("  python test_m3u8_extraction.py              # 交互式测试")
            print("  python test_m3u8_extraction.py <URL>        # 测试指定URL")
            print("  python test_m3u8_extraction.py --server <URL>  # 指定服务器地址")
            sys.exit(0)
        elif sys.argv[1] == '--server' and len(sys.argv) > 2:
            BASE_URL = sys.argv[2]
            if len(sys.argv) > 3:
                single_test(sys.argv[3])
            else:
                interactive_test()
        else:
            single_test(sys.argv[1])
    else:
        # 交互式测试
        interactive_test()

