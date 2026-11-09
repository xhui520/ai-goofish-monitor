"""
桌面启动入口
使用 PyInstaller 打包后作为单一可执行文件的入口，自动启动 FastAPI 服务并打开浏览器。
"""
import os
import sys
import time
import webbrowser
from pathlib import Path

import uvicorn

# PyInstaller 运行时资源目录：_MEIPASS；未打包时则为当前文件所在目录
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _prepare_environment() -> None:
    """确保工作目录和模块路径正确"""
    os.chdir(BASE_DIR)
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))


def run_app() -> None:
    """启动 FastAPI 应用并自动打开浏览器"""
    _prepare_environment()

    from src.app import app
    from src.infrastructure.config.settings import settings

    url = f"http://127.0.0.1:{settings.server_port}"

    # 服务就绪后再打开浏览器，避免慢机器上先到连接拒绝页
    import threading

    def _open_when_ready() -> None:
        import urllib.request

        while True:
            try:
                with urllib.request.urlopen(f"{url}/health", timeout=2):
                    break
            except Exception:
                time.sleep(0.5)
        webbrowser.open(url)

    threading.Thread(target=_open_when_ready, daemon=True).start()

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=settings.server_port,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    run_app()
