# 添加项目根目录到Python路径
import sys
import os

# 获取当前文件所在目录
current_dir = os.path.dirname(os.path.abspath(__file__))
# 将当前目录添加到sys.path
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

import uuid
import base64
import datetime
from typing import Optional, List, Dict, Any
from src.models.db import engine, Base, SessionLocal, Record
from src.utils.helpers import get_secret_key, load_config, get_file_icons_path
from src.utils.logger import get_logger, console_only_log
from itsdangerous import URLSafeTimedSerializer
import uvicorn
import secrets
import os
import datetime
import sched
import time
from threading import Thread
from sqlalchemy.orm import Session
from sqlalchemy import text
from src.api.auth import get_room_id_from_cookie
from src.api import auth, records

# 加载配置
config = load_config()

# 获取日志记录器
logger = get_logger('app')

# 根据debug_mode设置日志级别
# 只在直接运行时设置日志级别，避免uvicorn导入时重复设置
def setup_log_level():
    if config['server']['debug_mode']:
        # 开发模式：显示更多日志
        import logging
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG)
        uvicorn_access_logger = logging.getLogger('uvicorn.access')
        uvicorn_access_logger.setLevel(logging.INFO)
        logger.info("开发模式已启用，日志级别设置为DEBUG")
    else:
        # 在生产模式下，我们只设置uvicorn相关日志级别
        import logging
        uvicorn_access_logger = logging.getLogger('uvicorn.access')
        uvicorn_access_logger.setLevel(logging.WARNING)
        console_only_log("生产模式已启用，日志级别设置为WARNING")

# 只有在直接运行时才执行日志级别设置
if __name__ == "__main__":
    setup_log_level()

# 创建FastAPI应用
app = FastAPI()

# 添加CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 开发环境允许所有源，生产环境可指定具体域名
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有HTTP方法
    allow_headers=["*"],  # 允许所有HTTP头
)

# 创建会话序列化器
SECRET_KEY = get_secret_key()
serializer = URLSafeTimedSerializer(SECRET_KEY)

# 获取当前文件的目录（项目根目录）
current_dir = os.path.dirname(os.path.abspath(__file__))

# 挂载静态文件目录，static目录在src目录下，使用配置中的前缀
try:
    static_prefix = config["upload"].get('static_path_prefix', 'static')
    uploads_prefix = config["upload"].get('uploads_path_prefix', 'uploads')
except:
    static_prefix = 'static'
    uploads_prefix = 'uploads'

# 获取文件格式图标库路径
file_icons_path = get_file_icons_path()
# 确保文件格式图标库目录存在
file_icons_full_path = os.path.join(current_dir, file_icons_path)
os.makedirs(file_icons_full_path, exist_ok=True)

app.mount(f"/{static_prefix}", StaticFiles(directory=os.path.join(current_dir, "src", "static")), name="static")
# 挂载上传文件目录，uploads目录在项目根目录（与src同级），使用配置中的前缀
app.mount(f"/{uploads_prefix}", StaticFiles(directory=os.path.join(current_dir, "uploads")), name="uploads")
# 挂载文件格式图标库目录，使用配置中的路径
app.mount(f"/{file_icons_path}", StaticFiles(directory=file_icons_full_path), name="file_icons")

templates = Jinja2Templates(directory=os.path.join(current_dir, "src", "templates"))

# 创建数据库表
Base.metadata.create_all(bind=engine)

# 检查并添加缺失的client_id列
try:
    db = SessionLocal()
    # 检查records表是否有client_id列
    result = db.execute(text("PRAGMA table_info(records)"))
    columns = [row[1] for row in result]
    if 'client_id' not in columns:
        logger.info("检测到records表缺少client_id列，正在添加...")
        db.execute(text("ALTER TABLE records ADD COLUMN client_id VARCHAR(50)"))
        db.commit()
        logger.info("成功添加client_id列")
    db.close()
except Exception as e:
    logger.error(f"检查数据库表时出错: {e}")

def cleanup_empty_directories():
    """清理超过保留天数的空文件夹，支持新的目录结构"""
    logger = get_logger(__name__)
    try:
        # 获取上传目录路径
        uploads_path = os.path.join(current_dir, config["upload"]["uploads_path"])
        retention_days = config["server"].get("history_retention_days", 30)
        cutoff_date = datetime.datetime.now() - datetime.timedelta(days=retention_days)
        
        if os.path.exists(uploads_path):
            deleted_dirs_count = 0
            
            # 只清理daily目录下的过期空目录，不清理persistent目录
            daily_path = os.path.join(uploads_path, "daily")
            if os.path.exists(daily_path):
                # 递归清理daily目录下的空目录
                deleted_dirs_count = _cleanup_empty_subdirectories(daily_path, cutoff_date, retention_days, logger)
            
            # 处理旧格式的日期目录（向后兼容）
            deleted_dirs_count += _cleanup_legacy_date_dirs(uploads_path, cutoff_date, logger)
            
            if deleted_dirs_count > 0:
                logger.info(f"清理了 {deleted_dirs_count} 个超过 {retention_days} 天的空文件夹", extra={'client_id': 'server', 'event': 'cleanup_empty_dirs_summary', 'room_id': ''})
    except Exception as e:
        logger.error(f"清理空文件夹时出错: {e}", extra={'client_id': 'server', 'event': 'cleanup_empty_dir_error', 'room_id': ''})

def _cleanup_empty_subdirectories(base_path, cutoff_date, retention_days, logger):
    """
    递归清理过期的空子目录
    
    参数:
    base_path: 基础目录路径
    cutoff_date: 截止日期
    retention_days: 保留天数
    logger: 日志记录器
    
    返回:
    int: 清理的目录数量
    """
    deleted_count = 0
    
    # 检查目录是否存在
    if not os.path.exists(base_path) or not os.path.isdir(base_path):
        return deleted_count
    
    # 遍历子目录
    for item in os.listdir(base_path):
        item_path = os.path.join(base_path, item)
        
        if os.path.isdir(item_path):
            # 检查是否为年目录（4位数字）
            if len(item) == 4 and item.isdigit():
                year = int(item)
                # 递归清理年月目录
                deleted_count += _cleanup_month_directories(item_path, year, cutoff_date, retention_days, logger)
            else:
                # 对于其他类型的目录，递归清理
                deleted_count += _cleanup_empty_subdirectories(item_path, cutoff_date, retention_days, logger)
            
            # 检查当前目录是否为空，如果为空则删除
            if not os.listdir(item_path):
                try:
                    os.rmdir(item_path)
                    deleted_count += 1
                    logger.debug(f"清理空文件夹 {item_path}", extra={'client_id': 'server', 'event': 'cleanup_empty_dir', 'room_id': ''})
                except Exception as e:
                    logger.error(f"删除空文件夹 {item_path} 时出错: {e}", extra={'client_id': 'server', 'event': 'cleanup_empty_dir_error', 'room_id': ''})
    
    return deleted_count

def _cleanup_month_directories(year_path, year, cutoff_date, retention_days, logger):
    """
    清理年目录下的月目录
    
    参数:
    year_path: 年目录路径
    year: 年份
    cutoff_date: 截止日期
    retention_days: 保留天数
    logger: 日志记录器
    
    返回:
    int: 清理的目录数量
    """
    deleted_count = 0
    
    for month in os.listdir(year_path):
        month_path = os.path.join(year_path, month)
        
        if os.path.isdir(month_path) and len(month) == 2 and month.isdigit():
            month_num = int(month)
            # 递归清理日目录
            deleted_count += _cleanup_day_directories(month_path, year, month_num, cutoff_date, logger)
            
            # 检查月目录是否为空，如果为空则删除
            if not os.listdir(month_path):
                try:
                    os.rmdir(month_path)
                    deleted_count += 1
                    logger.debug(f"清理空月文件夹 {month_path}", extra={'client_id': 'server', 'event': 'cleanup_empty_dir', 'room_id': ''})
                except Exception as e:
                    logger.error(f"删除空月文件夹 {month_path} 时出错: {e}", extra={'client_id': 'server', 'event': 'cleanup_empty_dir_error', 'room_id': ''})
    
    return deleted_count

def _cleanup_day_directories(month_path, year, month, cutoff_date, logger):
    """
    清理月目录下的日目录
    
    参数:
    month_path: 月目录路径
    year: 年份
    month: 月份
    cutoff_date: 截止日期
    logger: 日志记录器
    
    返回:
    int: 清理的目录数量
    """
    deleted_count = 0
    
    for day in os.listdir(month_path):
        day_path = os.path.join(month_path, day)
        
        if os.path.isdir(day_path) and len(day) == 2 and day.isdigit():
            day_num = int(day)
            
            # 构建日期对象
            try:
                dir_date = datetime.datetime(year, month, day_num)
                
                # 检查是否超过保留天数
                if dir_date < cutoff_date:
                    # 检查目录是否为空
                    if not os.listdir(day_path):
                        try:
                            os.rmdir(day_path)
                            deleted_count += 1
                            logger.debug(f"清理空日文件夹 {day_path}", extra={'client_id': 'server', 'event': 'cleanup_empty_dir', 'room_id': ''})
                        except Exception as e:
                            logger.error(f"删除空日文件夹 {day_path} 时出错: {e}", extra={'client_id': 'server', 'event': 'cleanup_empty_dir_error', 'room_id': ''})
            except ValueError:
                # 无效日期，跳过
                continue
    
    return deleted_count

def _cleanup_legacy_date_dirs(uploads_path, cutoff_date, logger):
    """
    清理旧格式的日期目录（向后兼容）
    
    参数:
    uploads_path: 上传目录路径
    cutoff_date: 截止日期
    logger: 日志记录器
    
    返回:
    int: 清理的目录数量
    """
    deleted_count = 0
    
    for dir_name in os.listdir(uploads_path):
        dir_path = os.path.join(uploads_path, dir_name)
        
        # 检查是否是目录且名称符合旧格式日期(YYYYMMDD)
        if os.path.isdir(dir_path) and len(dir_name) == 8 and dir_name.isdigit():
            try:
                # 解析目录名称为日期
                dir_date = datetime.datetime.strptime(dir_name, "%Y%m%d")
                
                # 检查是否超过保留天数且为空文件夹
                if dir_date < cutoff_date:
                    # 检查目录是否为空
                    if not os.listdir(dir_path):
                        # 删除空文件夹
                        os.rmdir(dir_path)
                        deleted_count += 1
                        logger.debug(f"清理旧格式空文件夹 {dir_path}", extra={'client_id': 'server', 'event': 'cleanup_empty_dir', 'room_id': ''})
            except ValueError:
                # 如果目录名称不是有效日期格式，跳过
                continue
    
    return deleted_count

# 自动清理历史记录
def cleanup_old_records():
    """清理超过保留天数的历史记录"""
    logger = get_logger(__name__)
    try:
        db = SessionLocal()
        retention_days = config["server"].get("history_retention_days", 30)
        cutoff_date = datetime.datetime.now() - datetime.timedelta(days=retention_days)
        cutoff_timestamp = cutoff_date.strftime("%Y%m%d-%H%M%S.%f")[:-3]
        
        # 查询超期的记录，排除安全房间
        safe_rooms = config["server"].get("safe_rooms", [])
        # 确保 safe_rooms 是可迭代的
        if not safe_rooms:
            safe_rooms = []
        # 转换为字符串列表以进行数据库查询
        safe_rooms_str = [str(room).zfill(6) if isinstance(room, int) else room for room in safe_rooms]
        old_records = db.query(Record).filter(
            Record.upload_timestamp < cutoff_timestamp,
            ~Record.room_id.in_(safe_rooms_str)
        ).all()
        
        deleted_files_count = 0
        for record in old_records:
            # 如果是文件，删除文件
            if record.type == "file":
                uploads_path = os.path.join(current_dir, config["upload"]["uploads_path"])
                file_path = os.path.join(uploads_path, record.content)
                
                # 只删除非持久化文件（daily目录下的文件）
                # 检查文件路径是否包含"daily"，如果是则删除；如果包含"persistent"则跳过
                if os.path.exists(file_path):
                    if "persistent" not in file_path:
                        os.remove(file_path)
                        deleted_files_count += 1
                        # 确保输出的房间号是6位格式
                        formatted_room_id = str(record.room_id).zfill(6)
                        logger.info(f"清理文件 {file_path}", extra={'client_id': 'server', 'event': 'cleanup_file', 'room_id': formatted_room_id})
                    else:
                        # 确保输出的房间号是6位格式
                        formatted_room_id = str(record.room_id).zfill(6)
                        logger.info(f"跳过持久化文件清理 {file_path}", extra={'client_id': 'server', 'event': 'skip_persistent_file', 'room_id': formatted_room_id})
            # 删除数据库记录
            db.delete(record)
        
        db.commit()
        # 只记录有实际清理操作的日志，屏蔽无效信息
        if len(old_records) > 0 or deleted_files_count > 0:
            logger.info(f"清理了 {len(old_records)} 条超过 {retention_days} 天的历史记录，其中删除了 {deleted_files_count} 个文件", 
                      extra={'client_id': 'server', 'event': 'cleanup_records', 'room_id': 'all'})
        
        # 清理空文件夹
        cleanup_empty_directories()
    except Exception as e:
        logger.error(f"清理历史记录时出错: {e}", extra={'client_id': 'server', 'event': 'cleanup_error', 'room_id': ''})
    finally:
        db.close()

# 清理匿名房间超过24小时的记录
def cleanup_anonymous_room_records():
    """清理匿名房间中超过24小时的记录"""
    # 移除了日志记录，以减少日志文件的大小
    try:
        db = SessionLocal()
        # 匿名房间记录只保留24小时
        cutoff_date = datetime.datetime.now() - datetime.timedelta(hours=24)
        cutoff_timestamp = cutoff_date.strftime("%Y%m%d-%H%M%S.%f")[:-3]
        
        # 初始化 old_records 变量
        old_records = []
        
        # 查询匿名房间中超过24小时的记录
        anonymous_rooms = config["server"].get("anonymous_rooms", [])
        # 确保 anonymous_rooms 是可迭代的
        if not anonymous_rooms:
            anonymous_rooms = []
        # 转换为字符串列表以进行数据库查询
        anonymous_rooms_str = [str(room).zfill(6) if isinstance(room, int) else room for room in anonymous_rooms]
        if anonymous_rooms_str:
            old_records = db.query(Record).filter(
                Record.upload_timestamp < cutoff_timestamp,
                Record.room_id.in_(anonymous_rooms_str)
            ).all()
            
            for record in old_records:
                # 如果是文件，删除文件
                if record.type == "file":
                    uploads_path = os.path.join(current_dir, config["upload"]["uploads_path"])
                    file_path = os.path.join(uploads_path, record.content)
                    if os.path.exists(file_path):
                        os.remove(file_path)
                # 删除数据库记录
                db.delete(record)
            
            db.commit()
        logger = get_logger(__name__)
        # 只记录有实际清理操作的日志，屏蔽无效信息
        if len(old_records) > 0:
            logger.info(f"清理了匿名房间中 {len(old_records)} 条超过24小时的记录", extra={'client_id': 'server', 'event': 'cleanup_anonymous_room_records', 'room_id': '000000'})
    except Exception as e:
        # 仅保留错误日志，便于排查问题
        logger = get_logger(__name__)
        logger.error(f"清理匿名房间记录时出错: {e}", extra={'client_id': 'server', 'event': 'cleanup_anonymous_room_error', 'room_id': ''})
    finally:
        db.close()

# 设置调度器，定期执行清理任务
def cleanup_old_logs():
    """清理超过保留天数的旧日志文件"""
    logger = get_logger(__name__)
    try:
        # 获取日志目录路径
        log_dir = os.path.join(current_dir, "logs")
        # 从配置中获取日志保留天数，如果没有配置则默认为30天
        retention_days = config["server"].get("log_retention_days", 30)
        cutoff_date = datetime.datetime.now() - datetime.timedelta(days=retention_days)
        
        if os.path.exists(log_dir):
            deleted_logs_count = 0
            
            # 遍历日志目录下的所有文件
            for filename in os.listdir(log_dir):
                file_path = os.path.join(log_dir, filename)
                
                # 检查是否为文件且文件名符合日志格式 (iwebshelter_YYYYMMDD.log)
                if os.path.isfile(file_path) and filename.startswith("iwebshelter_") and filename.endswith(".log"):
                    # 文件名格式: iwebshelter_YYYYMMDD.log
                    try:
                        # 分割文件名，获取日期部分
                        date_str = filename.split('_')[1].split('.')[0]
                        file_date = datetime.datetime.strptime(date_str, "%Y%m%d")
                        
                        # 如果文件日期早于截止日期，则删除
                        if file_date < cutoff_date:
                            os.remove(file_path)
                            deleted_logs_count += 1
                            logger.debug(f"已删除过期日志文件: {filename}", extra={'client_id': 'server', 'event': 'cleanup_old_log', 'room_id': ''})
                    except (IndexError, ValueError) as e:
                        # 如果无法正确解析日期，跳过该文件
                        logger.debug(f"无法从文件名 {filename} 中解析日期，跳过: {str(e)}", extra={'client_id': 'server', 'event': 'skip_invalid_log_file', 'room_id': ''})
                    except Exception as e:
                        logger.error(f"处理日志文件 {filename} 时出错: {e}", extra={'client_id': 'server', 'event': 'cleanup_old_log_error', 'room_id': ''})
            
            if deleted_logs_count > 0:
                logger.info(f"清理了 {deleted_logs_count} 个超过 {retention_days} 天的日志文件", extra={'client_id': 'server', 'event': 'cleanup_old_logs_summary', 'room_id': ''})
    except Exception as e:
        logger.error(f"清理日志文件时出错: {e}", extra={'client_id': 'server', 'event': 'cleanup_old_logs_error', 'room_id': ''})


def schedule_cleanup_tasks():
    """设置定时清理任务"""
    scheduler = sched.scheduler(time.time, time.sleep)
    
    # 定义递归执行的匿名房间清理函数
    def run_cleanup_anonymous_room_records(sc):
        cleanup_anonymous_room_records()
        # 每5分钟执行一次
        sc.enter(300, 1, run_cleanup_anonymous_room_records, (sc,))
    
    # 定义递归执行的普通房间清理函数
    def run_cleanup_old_records(sc):
        cleanup_old_records()
        # 每60分钟执行一次
        sc.enter(3600, 1, run_cleanup_old_records, (sc,))
    
    # 定义递归执行的日志清理函数
    def run_cleanup_old_logs(sc):
        cleanup_old_logs()
        # 每24小时执行一次
        sc.enter(86400, 1, run_cleanup_old_logs, (sc,))
    
    # 启动匿名房间清理任务（立即执行）
    scheduler.enter(0, 1, run_cleanup_anonymous_room_records, (scheduler,))
    
    # 启动普通房间清理任务（立即执行）
    scheduler.enter(0, 1, run_cleanup_old_records, (scheduler,))
    
    # 启动日志清理任务（立即执行）
    scheduler.enter(0, 1, run_cleanup_old_logs, (scheduler,))
    
    # 运行调度器
    scheduler.run()

# 在后台线程中启动调度器
# 清理线程将在main函数中启动

# 注册路由
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(records.router, prefix="/api", tags=["records"])

# 导入WebSocket并定义端点
from fastapi import WebSocket, WebSocketDisconnect
from src.api.websocket import ConnectionManager

# 从websocket模块导入连接管理器，确保全局只有一个实例
from src.api.websocket import manager, router as websocket_router

# 包含WebSocket路由
app.include_router(websocket_router)

# 认证依赖直接从auth.py导入
# 注意：不再在app.py中定义重复的认证函数

# 添加异常处理器，将错误重定向到登录页面
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    # 对非API请求的错误重定向到登录页面
    if not request.url.path.startswith("/api/"):
        # 对于404、401等错误，重定向到登录页面
        if exc.status_code in [401, 403, 404, 307]:
            return RedirectResponse(url="/login")
        # 其他错误也重定向到登录页面
        return RedirectResponse(url="/login")
    # 对API请求返回相应的错误状态码
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

# 添加404异常处理器，专门处理路径不存在的情况
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    # 对非API请求重定向到登录页面
    if not request.url.path.startswith("/api/"):
        return RedirectResponse(url="/login")
    # 对API请求返回404错误
    return JSONResponse(status_code=404, content={"detail": "Not found"})

# 添加通用异常处理器，捕获所有未处理的异常
@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    # 对非API请求重定向到登录页面
    if not request.url.path.startswith("/api/"):
        return RedirectResponse(url="/login")
    # 对API请求返回500错误
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

# 首页重定向到登录页
@app.get("/")
async def root():
    return RedirectResponse(url="/login")

# 为标准图标文件提供直接路由，避免404错误


@app.get("/favicon.ico")
async def favicon():
    # 从static/icons目录提供favicon.ico
    favicon_path = os.path.join(current_dir, "src", "static", "icons", "favicon.ico")
    if os.path.exists(favicon_path):
        with open(favicon_path, "rb") as f:
            content = f.read()
        return Response(content=content, media_type="image/x-icon")
    raise HTTPException(status_code=404, detail="File not found")

@app.get("/apple-touch-icon.png")
async def apple_touch_icon():
    # 从static/icons目录提供apple-touch-icon.png
    icon_path = os.path.join(current_dir, "src", "static", "icons", "apple-touch-icon.png")
    if os.path.exists(icon_path):
        with open(icon_path, "rb") as f:
            content = f.read()
        return Response(content=content, media_type="image/png")
    raise HTTPException(status_code=404, detail="File not found")

@app.get("/apple-touch-icon-precomposed.png")
async def apple_touch_icon_precomposed():
    # 从static/icons目录提供apple-touch-icon-precomposed.png
    icon_path = os.path.join(current_dir, "src", "static", "icons", "apple-touch-icon-precomposed.png")
    if os.path.exists(icon_path):
        with open(icon_path, "rb") as f:
            content = f.read()
        return Response(content=content, media_type="image/png")
    raise HTTPException(status_code=404, detail="File not found")

@app.get("/apple-touch-icon-120x120.png")
async def apple_touch_icon_120x120():
    # 从static/icons目录提供apple-touch-icon-120x120.png
    icon_path = os.path.join(current_dir, "src", "static", "icons", "apple-touch-icon-120x120.png")
    if os.path.exists(icon_path):
        with open(icon_path, "rb") as f:
            content = f.read()
        return Response(content=content, media_type="image/png")
    raise HTTPException(status_code=404, detail="File not found")

@app.get("/apple-touch-icon-120x120-precomposed.png")
async def apple_touch_icon_120x120_precomposed():
    # 从static/icons目录提供apple-touch-icon-120x120-precomposed.png
    icon_path = os.path.join(current_dir, "src", "static", "icons", "apple-touch-icon-120x120-precomposed.png")
    if os.path.exists(icon_path):
        with open(icon_path, "rb") as f:
            content = f.read()
        return Response(content=content, media_type="image/png")
    raise HTTPException(status_code=404, detail="File not found")

# 登录页面
@app.get("/login")
async def login_page(request: Request):
    # 传递静态路径前缀给模板
    static_prefix = config["upload"].get('static_path_prefix', 'static')
    uploads_prefix = config["upload"].get('uploads_path_prefix', 'uploads')
    
    # 获取匿名房间ID列表
    anonymous_rooms = config["server"].get("anonymous_rooms", [])
    
    # 获取项目名称
    project_name = config["server"].get("project_name", "iWebShelter")
    
    # 获取历史记录保留天数
    history_retention_days = config["server"].get("history_retention_days", 90)
    
    return templates.TemplateResponse("login.html", {
        "request": request,
        "static_prefix": static_prefix,
        "uploads_prefix": uploads_prefix,
        "anonymous_rooms": anonymous_rooms,
        "project_name": project_name,
        "history_retention_days": history_retention_days
    })

# 移除直接的登录表单提交路由，使用/api/login API代替

# 主页面，需要会话验证
@app.get("/index")
async def index_page(request: Request, room_id: str = Depends(get_room_id_from_cookie)):
    """
    主页面，需要有效的会话cookie才能访问
    """
    # 传递静态路径前缀给模板
    static_prefix = config["upload"].get('static_path_prefix', 'static')
    uploads_prefix = config["upload"].get('uploads_path_prefix', 'uploads')
    
    # 从配置中获取项目名称
    project_name = config["server"].get("project_name", "iWebShelter")
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "room_id": room_id,
        "static_prefix": static_prefix,
        "uploads_prefix": uploads_prefix,
        "server_name": project_name,
        "project_name": project_name
    })

if __name__ == "__main__":
    import socket
    
    # 获取本地IP地址
    def get_local_ips():
        ips = []
        try:
            # 获取主机名
            host_name = socket.gethostname()
            # 获取所有IP地址
            all_ips = socket.gethostbyname_ex(host_name)[2]
            for ip in all_ips:
                # 过滤回环地址
                if not ip.startswith('127.'):
                    ips.append(ip)
        except Exception as e:
            print(f"获取IP地址时出错: {e}")
        return ips
    
    local_ips = get_local_ips()
    port = config["server"]["port"]
    console_only_log("=" * 50)
    console_only_log(f"iWebShelter 服务器启动中...")
    console_only_log(f"debug_mode: {config['server']['debug_mode']}")
    console_only_log(f"访问链接:")
    console_only_log(f"  - 本地回环: http://127.0.0.1:{port}/login")
    if local_ips:
        for ip in local_ips:
            console_only_log(f"  - 网络IP: http://{ip}:{port}/login")
    else:
        console_only_log(f"  - 网络IP: 未获取到本地IP")
    console_only_log("=" * 50)
    
    # 检查端口占用
    import sys
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("0.0.0.0", port))
        sock.close()
        console_only_log(f"端口 {port} 可用")
    except OSError as e:
        logger.error(f"端口 {port} 被占用: {e}")
        sys.exit(1)
    
    # 启动清理线程
    cleanup_thread = Thread(target=schedule_cleanup_tasks, daemon=True)
    cleanup_thread.start()
    
    # 启动应用
    try:
        console_only_log("Starting uvicorn server...")
        # 当启用debug_mode时，使用导入字符串方式运行，或者禁用reload功能
        if config["server"]["debug_mode"]:
            # 不使用reload，直接传递app对象
            uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
        else:
            uvicorn.run(app, host="0.0.0.0", port=port)
        logger.info("Uvicorn server stopped.")
    except Exception as e:
        import traceback
        logger.error(f"Uvicorn server error: {e}")
        traceback.print_exc()
