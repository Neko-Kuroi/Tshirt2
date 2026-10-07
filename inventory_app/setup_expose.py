import os
import shutil
#import hashlib
#import tempfile
import time
import base64
import subprocess
import threading
import sys
import re
#from IPython.display import HTML, display
import logging
import requests # Import the requests library

exposed_url = ""
# ログの設定
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

def cleanup_old_processes():
    """以前の古いプロセス（Flaskやトンネル）が残っている場合は強制終了してポートを解放"""
    print("🧹 古いプロセスをクリーンアップしています...")
    os.system("pkill -f 'run.py' 2>/dev/null")
    os.system("pkill -f 'bore' 2>/dev/null")
    os.system("pkill -f 'cloudflared' 2>/dev/null")
    time.sleep(1)

# ログ読み取り関数
def read_process_output(process, stream, prefix="", line_callback=None):
    """プロセスの出力をリアルタイムで読み取り、表示する関数"""
    try:
        for line in iter(stream.readline, ""):
            print(f"{prefix}{line.strip()}")
            if line_callback:
                try:
                    line_callback(line)
                except Exception:
                    pass
    except (ValueError, OSError):
        pass
    try:
        stream.close()
    except Exception as e:
        logger.debug(f"Error closing stream in read_process_output: {e}")


def setup_bore_tunnel():
    """Rust製のboreトンネルの設定"""
    print("🦀 Bore トンネルをセットアップしています...")

    # boreのダウンロードとインストール
    os.system('sudo wget -nc https://github.com/ekzhang/bore/releases/download/v0.6.0/bore-v0.6.0-x86_64-unknown-linux-musl.tar.gz')
    os.system('sudo tar -zxvf bore-v0.6.0-x86_64-unknown-linux-musl.tar.gz')
    os.system('sudo chmod 764 bore')

    # 元のFastAPI起動コード (コメントアウト)
    # flask_process = subprocess.Popen(
    #     [sys.executable, "-m", "uvicorn", "main:app", "--reload"],
    #     stdout=subprocess.PIPE,
    #     stderr=subprocess.PIPE,
    #     text=True,
    #     bufsize=1
    # )

    # 新しいFlask起動コード (python run.py)
    print("🚀 Flask アプリケーションを起動しています (python run.py)...")
    flask_process = subprocess.Popen(
        [sys.executable, "run.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=CURRENT_DIR,
        text=True,
        bufsize=1
    )

    flask_stdout_thread = threading.Thread(target=read_process_output, args=(flask_process, flask_process.stdout, "[FLASK_OUT] "))
    flask_stderr_thread = threading.Thread(target=read_process_output, args=(flask_process, flask_process.stderr, "[FLASK_ERR] "))
    flask_stdout_thread.daemon = True
    flask_stderr_thread.daemon = True
    flask_stdout_thread.start()
    flask_stderr_thread.start()

    if not wait_for_flask_server(process=flask_process, port=5000, timeout=60):
        print("❌ Flaskアプリケーションが指定時間内に起動しませんでした。")
        flask_process.terminate()
        flask_stdout_thread.join(timeout=5)
        flask_stderr_thread.join(timeout=5)
        return None, None, ""

    # boreトンネルの起動 (ポート 5000 へトンネル)
    print("🌐 bore トンネルを開始しています...")
    bore_process = subprocess.Popen(['sudo', './bore', 'local', '5000', '--to', 'bore.pub'],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,
                                   text=True,
                                   bufsize=1
                                   )

    url_event = threading.Event()
    extracted_bore_url = []

    def on_bore_line(line):
        if not url_event.is_set():
            match = re.search(r'(bore\.pub:\d+)', line)
            if match:
                extracted_bore_url.append(match.group(0).strip())
                url_event.set()

    bore_stdout_thread = threading.Thread(target=read_process_output, args=(bore_process, bore_process.stdout, "[BORE_OUT] ", on_bore_line))
    bore_stderr_thread = threading.Thread(target=read_process_output, args=(bore_process, bore_process.stderr, "[BORE_ERR] "))
    bore_stdout_thread.daemon = True
    bore_stderr_thread.daemon = True
    bore_stdout_thread.start()
    bore_stderr_thread.start()

    print("🔍 トンネルURLを待機しています...")
    start_time = time.time()
    timeout = 180

    while time.time() - start_time < timeout:
        if url_event.wait(timeout=0.5):
            break
        if bore_process.poll() is not None:
            print("Boreプロセスが予期せず終了しました。")
            break

    if extracted_bore_url:
        url = extracted_bore_url[0]
        print(f"✅ トンネルが開始されました: {url}")
    else:
        url = ""
        print("⚠️ トンネルURLの取得に失敗しました。")

    return flask_process, bore_process, url

def setup_cloudflare_tunnel():
    """Cloudflare Tunnelの設定"""
    print("☁️ Cloudflare Tunnel をセットアップしています...")

    os.system('sudo wget -nc https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb')
    os.system('sudo dpkg -i cloudflared-linux-amd64.deb 2>/dev/null')

    # 元のFastAPI起動コード (コメントアウト)
    # flask_process = subprocess.Popen(
    #     [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"],
    #     stdout=subprocess.PIPE,
    #     stderr=subprocess.PIPE,
    #     text=True,
    #     bufsize=1
    # )

    # 新しいFlask起動コード (python run.py)
    print("🚀 Flask アプリケーションを起動しています (python run.py)...")
    flask_process = subprocess.Popen(
        [sys.executable, "run.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=CURRENT_DIR,
        text=True,
        bufsize=1
    )

    flask_stdout_thread = threading.Thread(target=read_process_output, args=(flask_process, flask_process.stdout, "[FLASK_OUT] "))
    flask_stderr_thread = threading.Thread(target=read_process_output, args=(flask_process, flask_process.stderr, "[FLASK_ERR] "))
    flask_stdout_thread.daemon = True
    flask_stderr_thread.daemon = True
    flask_stdout_thread.start()
    flask_stderr_thread.start()

    if not wait_for_flask_server(process=flask_process, port=5000, timeout=60):
        print("❌ Flaskアプリケーションが指定時間内に起動しませんでした。")
        flask_process.terminate()
        flask_stdout_thread.join(timeout=5)
        flask_stderr_thread.join(timeout=5)
        return None, None, ""

    cloudflared_bin = shutil.which("cloudflared") or (
        "/usr/local/bin/cloudflared"
        if os.path.exists("/usr/local/bin/cloudflared")
        else "/usr/bin/cloudflared"
    )

    # Cloudflareトンネルの起動 (ポート 5000 へトンネル)
    print("🌐 Cloudflare トンネルを開始しています...")
    tunnel_process = subprocess.Popen([cloudflared_bin, 'tunnel', '--url', 'http://127.0.0.1:5000'],
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE,
                                     text=True,
                                     bufsize=1)

    url_event = threading.Event()
    extracted_cf_url = []

    def on_cf_line(line):
        if not url_event.is_set():
            match = re.search(r'https://[a-zA-Z0-9-]+\.trycloudflare\.com', line)
            if match:
                extracted_cf_url.append(match.group(0).strip())
                url_event.set()

    tunnel_stdout_thread = threading.Thread(target=read_process_output, args=(tunnel_process, tunnel_process.stdout, "[CF_OUT] ", on_cf_line))
    tunnel_stderr_thread = threading.Thread(target=read_process_output, args=(tunnel_process, tunnel_process.stderr, "[CF_ERR] ", on_cf_line))
    tunnel_stdout_thread.daemon = True
    tunnel_stderr_thread.daemon = True
    tunnel_stdout_thread.start()
    tunnel_stderr_thread.start()

    print("🔍 トンネルURLを待機しています...")
    start_time = time.time()
    timeout = 180

    while time.time() - start_time < timeout:
        if url_event.wait(timeout=0.5):
            break
        if tunnel_process.poll() is not None:
            print("Cloudflare Tunnelプロセスが予期せず終了しました。")
            break

    if extracted_cf_url:
        url = extracted_cf_url[0]
        print(f"✅ Cloudflare トンネルが開始されました: {url}")
    else:
        url = ""
        print("⚠️ CloudflareトンネルURLの取得に失敗しました。")

    return flask_process, tunnel_process, url

def wait_for_flask_server(process=None, port=5000, timeout=60):
    """flaskサーバーが起動し、リクエストに応答するのを待機します。"""
    url = f"http://127.0.0.1:{port}"
    print(f"Waiting for Flask server to start at {url}...")
    start_time = time.time()
    while time.time() - start_time < timeout:
        if process and process.poll() is not None:
            print(f"❌ Flaskプロセスが予期せず終了しました (終了コード: {process.poll()})。")
            return False
        try:
            response = requests.get(url, timeout=2, allow_redirects=True)
            if response.status_code < 500:
                print(f"✅ Flask server is up and running! Status code: {response.status_code}")
                return True
        except requests.exceptions.RequestException:
            pass
        except Exception as e:
            print(f"Error during Flask server health check: {e}")
            pass
        time.sleep(1)
    print(f"❌ Flask server did not respond within {timeout} seconds.")
    return False


def get_colab_external_ip():
    """Colabの外部IPアドレスを取得します。"""
    try:
        result = subprocess.run(['curl', 'ipinfo.io/ip'], capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except Exception as e:
        print(f"⚠️ 外部IPアドレスの取得に失敗しました: {e}")
        return "UNKNOWN_IP"

selected_tunnel_service = ""
while True:
    print("\n--- トンネル方法を選択してください ---")
    print("1. Bore")
    print("2. Cloudflared")
    choice = input("選択 (1/2): ").strip()

    if choice == '1':
        selected_tunnel_service = "bore"
        break
    elif choice == '2':
        selected_tunnel_service = "cloudflared"
        break
    else:
        print("無効な選択です。1 または 2 を入力してください。")

cleanup_old_processes()

if selected_tunnel_service == "bore":
    flask_process, tunnel_process, exposed_url = setup_bore_tunnel()
elif selected_tunnel_service == "cloudflared":
    flask_process, tunnel_process, exposed_url = setup_cloudflare_tunnel()

print(f"\nColab 外部IPアドレス: {get_colab_external_ip()}")
print(f"expose : {exposed_url}")

# メインプロセスを維持し、終了時に子プロセスを終了
if flask_process and tunnel_process:
    try:
        print("\n🟢 サーバ維持中... 終了するには Ctrl+C を押してください。")
        while True:
            if flask_process.poll() is not None:
                print(f"❌ Flaskプロセスが停止しました (コード: {flask_process.poll()})")
                break
            if tunnel_process.poll() is not None:
                print(f"❌ トンネルプロセスが停止しました (コード: {tunnel_process.poll()})")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n👋 終了シグナルを受信しました。プロセスを停止しています...")
    finally:
        for proc in [flask_process, tunnel_process]:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        cleanup_old_processes()
