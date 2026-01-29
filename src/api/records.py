from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Body, Request
from sqlalchemy.orm import Session
from ..models.db import get_db, Record
from ..utils.helpers import generate_record_index, create_date_directory, handle_filename_conflict, get_upload_path, load_config, create_directory_by_type
from ..utils.logger import get_logger
import os
import datetime
import time

# 导入会话验证依赖
from .auth import get_room_id_from_cookie

router = APIRouter()
logger = get_logger(__name__)

@router.get("/records/{room_id}")
async def get_records(request: Request, room_id: str, db: Session = Depends(get_db)):
    """
    获取指定房间的历史记录
    """
    # 开始计时
    start_time = time.time()
    
    # 格式化房间号为6位
    formatted_room_id = room_id.zfill(6) if isinstance(room_id, str) and room_id.isdigit() else str(room_id).zfill(6)
    
    # 手动进行会话验证，确保异常处理正确
    from fastapi.responses import RedirectResponse
    cookie_value = request.cookies.get("room_session")
    if not cookie_value:
        logger.warning("未找到会话cookie，重定向到登录页", extra={'event': 'session_missing', 'room_id': formatted_room_id})
        return RedirectResponse(url="/login")
    
    from app import serializer
    try:
        session_room_id = serializer.loads(cookie_value, max_age=3600)  # 1小时过期
        # 确保会话中的房间号也是字符串格式且为6位
        if isinstance(session_room_id, int):
            session_room_id = str(session_room_id).zfill(6)
        elif isinstance(session_room_id, str):
            session_room_id = session_room_id.zfill(6)
            
        # 验证用户是否有权访问该房间的记录
        if session_room_id != formatted_room_id:
            logger.warning("房间ID不匹配，拒绝访问", extra={'event': 'room_id_mismatch', 'room_id': formatted_room_id})
            raise HTTPException(status_code=403, detail="无权访问此房间的记录")
    except HTTPException:
        raise
    except:
        # 确保输出的房间号是6位格式
        formatted_room_id = room_id.zfill(6) if isinstance(room_id, str) and room_id.isdigit() else str(room_id).zfill(6)
        logger.warning("无效的会话cookie，重定向到登录页", extra={'event': 'invalid_session', 'room_id': formatted_room_id})
        return RedirectResponse(url="/login")
    # 加载配置
    config = load_config()
    # 检查是否是匿名房间
    anonymous_rooms = config["server"].get("anonymous_rooms", [])
    # 确保匿名房间ID是字符串格式
    anonymous_rooms_str = [str(room).zfill(6) if isinstance(room, int) else room for room in anonymous_rooms]
    
    # 查询记录，移除日志记录以减少信息过载
    records = db.query(Record).filter(Record.room_id == room_id).order_by(Record.record_index.desc()).all()
    
    # 处理记录，在匿名房间中将client_id显示为"匿名"
    processed_records = []
    for record in records:
        client_id = record.client_id
        if room_id in anonymous_rooms_str:

            client_id = "匿名"
        
        processed_records.append({
            "id": record.id,
            "room_id": record.room_id,
            "record_index": record.record_index,
            "upload_timestamp": record.upload_timestamp,
            "type": record.type,
            "content": record.content,
            "original_filename": record.original_filename,
            "file_extension": record.file_extension,
            "file_size": record.file_size,
            "client_id": client_id
        })
    
    # 获取是否启用删除功能的配置（按房间）
    disable_record_deletion_rooms = config["server"].get("disable_record_deletion_rooms", [])
    # 确保房间ID格式一致
    disable_record_deletion_rooms_str = [str(room).zfill(6) if isinstance(room, int) else room for room in disable_record_deletion_rooms]
    enable_record_deletion = room_id not in disable_record_deletion_rooms_str
    
    # 计算处理时间
    processing_time = (time.time() - start_time) * 1000  # 转换为毫秒
    
    # 移除查询完成日志记录以减少信息过载
    
    return {"status": "success", "records": processed_records, "enable_record_deletion": enable_record_deletion}

@router.post("/send_text")
async def send_text(request: Request, content: str = Body(...), client_id: str = Body(None), db: Session = Depends(get_db)):
    """
    发送文本内容
    """
    # 开始计时
    start_time = time.time()
    
    # 手动进行会话验证，确保异常处理正确
    from fastapi.responses import RedirectResponse
    cookie_value = request.cookies.get("room_session")
    if not cookie_value:
        logger.warning("未找到会话cookie", extra={'event': 'session_missing', 'client_id': client_id})
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    from app import serializer
    try:
        room_id_raw = serializer.loads(cookie_value, max_age=3600)  # 1小时过期
        # 确保room_id为字符串格式，不足6位则补零
        room_id = str(room_id_raw).zfill(6) if isinstance(room_id_raw, int) else room_id_raw
    except:
        logger.warning("无效的会话cookie，重定向到登录页", extra={'event': 'invalid_session', 'client_id': client_id})
        return RedirectResponse(url="/login")
    if not content.strip():
        raise HTTPException(status_code=400, detail="文本内容不能为空")
    
    # 加载配置
    config = load_config()
    # 检查是否是匿名房间
    anonymous_rooms = config["server"].get("anonymous_rooms", [])
    if room_id in anonymous_rooms:
        client_id = "匿名"
    
    # 创建记录
    record_index = generate_record_index(room_id)
    upload_timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S.%f")[:-3]
    
    record = Record(
        room_id=room_id,
        record_index=record_index,
        upload_timestamp=upload_timestamp,
        type="text",
        content=content,
        client_id=client_id
    )
    
    db.add(record)
    db.commit()
    db.refresh(record)
    
    # 确保输出的房间号是6位格式
    formatted_room_id = room_id.zfill(6) if isinstance(room_id, str) and room_id.isdigit() else str(room_id).zfill(6)
    # 获取用户ID
    user_id = request.cookies.get('user_uuid', client_id)
    # 添加文本内容摘要，避免过长日志，并将换行符替换为转义字符\n
    # 先截取内容，并替换换行符为\n
    truncated_content = content[:50] if len(content) > 50 else content
    content_summary = truncated_content.replace('\n', '\\n') + ('...' if len(content) > 50 else '')
    logger.info(f"添加文本信息：{content_summary}", extra={'user_id': user_id, 'event': 'add_text', 'room_id': formatted_room_id, 'content_summary': content_summary})
    
    # 计算处理时间
    processing_time = (time.time() - start_time) * 1000  # 转换为毫秒
    
    # 记录发送完成信息
    logger.info(f"文本信息发送完成：长度 {len(content)}字符, 耗时 {processing_time:.2f}ms",
                extra={'event': 'text_message_sent', 'room_id': formatted_room_id,
                       'record_id': record.id, 'content_length': len(content),
                       'processing_time_ms': processing_time, 'user_id': user_id})
    
    return {"status": "success", "record": {
        "id": record.id,
        "room_id": record.room_id,
        "record_index": record.record_index,
        "upload_timestamp": record.upload_timestamp,
        "type": record.type,
        "content": record.content
    }}

@router.post("/upload_file")
async def upload_file(request: Request, file: UploadFile = File(...), 
                    client_id: str = Form(None), is_persistent: bool = Form(False), 
                    db: Session = Depends(get_db)):
    """
    上传文件
    
    参数：
    request: 请求对象，用于会话验证
    file: 上传的文件
    client_id: 客户端ID
    is_persistent: 是否为持久化文件，默认为False
    """
    # 开始计时
    start_time = time.time()
    
    # 手动进行会话验证，确保异常处理正确
    from fastapi.responses import RedirectResponse
    cookie_value = request.cookies.get("room_session")
    if not cookie_value:
        logger.warning("未找到会话cookie", extra={'event': 'session_missing', 'client_id': client_id})
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    from app import serializer
    try:
        room_id = serializer.loads(cookie_value, max_age=3600)  # 1小时过期
    except:
        logger.warning("无效的会话cookie", extra={'event': 'invalid_session', 'client_id': client_id})
        raise HTTPException(status_code=401, detail="Not authenticated")
    # 加载配置
    config = load_config()
    
    # 读取文件上传配置
    # max_file_size以MB为单位
    max_file_size_mb = config["upload"].get("max_file_size", 50)  # 默认50MB
    # 转换为字节
    max_file_size = max_file_size_mb * 1024 * 1024
    allowed_extensions = config["upload"].get("allowed_extensions", [])
    
    # 读取文件内容
    content = await file.read()
    
    # 计算文件读取时间
    read_time = (time.time() - start_time) * 1000  # 转换为毫秒
    
    # 检查文件大小
    file_size = len(content)
    formatted_room_id = str(room_id).zfill(6)
    user_id = request.cookies.get('user_uuid', client_id)
    logger.debug(f"验证文件大小: {file_size/(1024*1024):.2f}MB / {max_file_size_mb}MB", extra={'user_id': user_id, 'event': 'validate_file_size', 'room_id': formatted_room_id, 'upload_filename': file.filename, 'file_size': file_size})
    
    if file_size > max_file_size:
        logger.warning(f"文件大小超过限制: {file_size/(1024*1024):.2f}MB > {max_file_size_mb}MB", extra={'user_id': user_id, 'event': 'file_size_exceeded', 'room_id': formatted_room_id, 'upload_filename': file.filename, 'file_size': file_size})
        raise HTTPException(status_code=400, detail=f"文件大小超过限制。最大允许大小: {max_file_size_mb} MB")
    
    # 获取文件扩展名
    file_extension = os.path.splitext(file.filename)[1][1:].lower()
    
    # 检查文件扩展名
    # 如果allowed_extensions为空列表或None，则不限制文件类型
    if allowed_extensions and len(allowed_extensions) > 0:
        # 转换为小写并统一格式
        allowed_exts_lower = [ext.lower() for ext in allowed_extensions]
        # 检查扩展名是否在允许列表中（处理带点和不带点的情况）
        if f".{file_extension}" not in allowed_exts_lower and file_extension not in allowed_exts_lower:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型。允许的文件类型: {allowed_extensions}")
    else:
        # 允许所有文件类型上传
        pass
        
        # 确保输出的房间号是6位格式
        formatted_room_id = str(room_id).zfill(6)
        user_id = request.cookies.get('user_uuid', client_id)
        logger.info(f"文件上传：{file.filename}", extra={'user_id': user_id, 'event': 'upload_start', 'room_id': formatted_room_id, 'upload_filename': file.filename})
    
    # 创建目录（根据是否持久化选择不同的目录结构）
    date_dir = create_directory_by_type(is_persistent=is_persistent)
    
    # 处理文件名冲突
    filename = handle_filename_conflict(date_dir, file.filename)
    
    # 保存文件
    file_path = os.path.join(date_dir, filename)
    try:
        # 记录文件写入开始时间
        write_start_time = time.time()
        
        with open(file_path, "wb") as f:
            f.write(content)
        
        # 计算写入时间
        write_time = (time.time() - write_start_time) * 1000  # 转换为毫秒
        
        # 移除文件写入磁盘的日志记录以减少信息过载
    except Exception as e:
        logger.error(f"文件写入失败: {str(e)}", extra={'user_id': user_id, 'event': 'file_write_error', 'room_id': formatted_room_id, 'filename': filename})        
        raise HTTPException(status_code=500, detail="文件保存失败")
    
    # 创建记录
    record_index = generate_record_index(room_id)
    upload_timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S.%f")[:-3]
    
    # 存储相对路径
    relative_path = os.path.relpath(file_path, "uploads")
    
    # 检查是否是匿名房间
    anonymous_rooms = config["server"].get("anonymous_rooms", [])
    if room_id in anonymous_rooms:
        client_id = "匿名"
    
    record = Record(
        room_id=room_id,
        record_index=record_index,
        upload_timestamp=upload_timestamp,
        type="file",
        content=relative_path,
        original_filename=file.filename,
        file_extension=file_extension,
        file_size=file_size,
        client_id=client_id
    )
    
    db.add(record)
    db.commit()
    db.refresh(record)
    
    # 确保输出的房间号是6位格式
    formatted_room_id = str(room_id).zfill(6)
    user_id = request.cookies.get('user_uuid', client_id)
    logger.info(f"上传成功：{file.filename}", extra={'user_id': user_id, 'event': 'upload_file', 'room_id': formatted_room_id, 'filename': file.filename, 'file_size': file_size})
    
    # 计算总处理时间
    total_processing_time = (time.time() - start_time) * 1000  # 转换为毫秒
    
    # 记录上传完成信息，添加user_id字段
    user_id = request.cookies.get('user_uuid', client_id)
    logger.info(f"传输情况：大小 {file_size / (1024 * 1024):.2f}MB, 耗时 {total_processing_time:.2f}ms",
                extra={'user_id': user_id, 'event': 'file_upload_completed', 'room_id': formatted_room_id,
                       'filename': file.filename, 'file_size_bytes': file_size,
                       'processing_time_ms': total_processing_time, 'record_id': record.id})
    
    return {"status": "success", "record": {
        "id": record.id,
        "room_id": record.room_id,
        "record_index": record.record_index,
        "upload_timestamp": record.upload_timestamp,
        "type": record.type,
        "content": record.content,
        "original_filename": record.original_filename,
        "file_extension": record.file_extension,
        "file_size": record.file_size
    }}

@router.delete("/delete_record/{record_id}")
async def delete_record(request: Request, record_id: int, db: Session = Depends(get_db)):
    """
    删除记录
    """
    # 开始计时
    start_time = time.time()
    
    # 手动进行会话验证，确保异常处理正确
    from fastapi.responses import RedirectResponse
    cookie_value = request.cookies.get("room_session")
    if not cookie_value:
        logger.warning("未找到会话cookie，重定向到登录页", extra={'event': 'session_missing', 'record_id': record_id})
        return RedirectResponse(url="/login")
    
    from app import serializer
    try:
        room_id = serializer.loads(cookie_value, max_age=3600)  # 1小时过期
    except:
        logger.warning("无效的会话cookie，重定向到登录页", extra={'event': 'invalid_session', 'record_id': record_id})
        return RedirectResponse(url="/login")
    record = db.query(Record).filter(Record.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="记录不存在")
    
    # 验证用户是否有权删除该房间的记录
    # 确保比较时都是字符串格式
    record_room_id_str = str(record.room_id).zfill(6) if isinstance(record.room_id, int) else record.room_id
    if record_room_id_str != room_id:
        # 确保输出的房间号是6位格式
        formatted_room_id = str(record.room_id).zfill(6)
        logger.warning("房间ID不匹配，拒绝删除", extra={'event': 'room_id_mismatch', 'record_id': record_id, 'room_id': formatted_room_id})
        raise HTTPException(status_code=403, detail="无权删除此记录")
    
    # 加载配置
    config = load_config()
    # 检查是否启用删除功能（按房间）
    disable_record_deletion_rooms = config["server"].get("disable_record_deletion_rooms", [])
    # 确保房间ID格式一致
    disable_record_deletion_rooms_str = [str(room).zfill(6) if isinstance(room, int) else room for room in disable_record_deletion_rooms]
    enable_record_deletion = room_id not in disable_record_deletion_rooms_str
    
    if not enable_record_deletion:
        raise HTTPException(status_code=403, detail="删除功能未启用")
    
    # 检查是否是匿名房间
    anonymous_rooms = config["server"].get("anonymous_rooms", [])
    
    # 统一显示client_id
    client_id = record.client_id
    if record.room_id in anonymous_rooms:
        client_id = "匿名"
    
    # 如果是文件，先删除文件
    if record.type == "file":
        uploads_path = get_upload_path()
        file_path = os.path.join(uploads_path, record.content)
        if os.path.exists(file_path):
            os.remove(file_path)
            # 确保输出的房间号是6位格式
            formatted_room_id = str(record.room_id).zfill(6)
            user_id = request.cookies.get('user_uuid', client_id)
            logger.info(f"文件删除成功：{record.original_filename}", extra={'user_id': user_id, 'event': 'delete_file', 'room_id': formatted_room_id, 'filename': record.original_filename, 'record_id': record_id})
            
            # 检查并删除可能为空的父目录
            try:
                # 获取文件所在的目录
                file_dir = os.path.dirname(file_path)
                # 只检查daily目录下的空目录，不处理persistent目录
                if "daily" in file_dir:
                    # 递归检查并删除空目录（从最内层开始）
                    while True:
                        # 确保目录存在且为daily目录下的子目录
                        if os.path.exists(file_dir) and os.path.isdir(file_dir) and "daily" in file_dir:
                            if not os.listdir(file_dir):
                                os.rmdir(file_dir)
                                # 获取父目录
                                file_dir = os.path.dirname(file_dir)
                                # 如果到达daily目录或uploads根目录，则停止
                                if os.path.basename(file_dir) == "daily" or os.path.basename(file_dir) == "uploads":
                                    break
                            else:
                                # 目录不为空，停止
                                break
                        else:
                            break
            except Exception as e:
                # 确保输出的房间号是6位格式
                formatted_room_id = str(record.room_id).zfill(6)
                logger.error(f"检查空目录时出错: {e}", extra={'client_id': client_id, 'event': 'delete_dir_error', 'room_id': formatted_room_id})
    
    # 删除数据库记录
    db.delete(record)
    db.commit()
    
    # 确保输出的房间号是6位格式
    formatted_room_id = str(record.room_id).zfill(6)
    # 获取用户ID
    user_id = request.cookies.get('user_uuid', client_id)
    # 添加记录类型和内容摘要到日志
    record_type = record.type
    content_type = '文件' if record_type == 'file' else '文本'
    # 为文本类型添加换行符替换功能
    if record_type == 'file':
        content_summary = record.original_filename
    else:
        # 先截取内容，再替换换行符，最后添加省略号
        truncated_content = record.content[:50] if len(record.content) > 50 else record.content
        content_summary = truncated_content.replace('\n', '\\n') + ('...' if len(record.content) > 50 else '')
    logger.info(f"信息删除成功：{content_type} = {content_summary}", extra={'user_id': user_id, 'event': 'delete_text', 'room_id': formatted_room_id, 'record_id': record_id, 'record_type': record_type})
    
    # 通过WebSocket向同一房间的所有客户端广播删除消息
    # 导入连接管理器
    from ..api.websocket import manager
    import asyncio
    
    # 添加详细的上下文日志
    formatted_room_id = str(record.room_id).zfill(6)
    logger.debug(f"准备通过WebSocket广播删除消息，房间: {formatted_room_id}, 记录ID: {record_id}, 类型: {record_type}", 
                extra={'user_id': user_id, 'event': 'ws_broadcast_prepare', 'room_id': formatted_room_id, 'record_id': record_id, 'record_type': record_type})
    
    # 创建删除消息
    delete_message = {
        "type": "record_delete",
        "record_id": record_id,
        "room_id": str(record.room_id)
    }
    
    # 创建异步任务广播删除消息
    try:
        asyncio.create_task(manager.broadcast(str(record.room_id), delete_message))
        logger.debug(f"WebSocket广播任务已创建，消息: {delete_message}", 
                    extra={'client_id': client_id, 'event': 'ws_broadcast_created', 'room_id': formatted_room_id})
    except Exception as e:
        logger.error(f"创建WebSocket广播任务失败: {e}", 
                    extra={'client_id': client_id, 'event': 'ws_broadcast_error', 'room_id': formatted_room_id})
    
    # 计算处理时间
    processing_time = (time.time() - start_time) * 1000  # 转换为毫秒
    
    # 记录删除完成信息
    logger.info(f"记录删除完成：记录ID{record_id}, 类型 {record_type}, 耗时 {processing_time:.2f}ms",
                extra={'user_id': user_id, 'event': 'record_deleted_completed', 'room_id': formatted_room_id,
                       'record_id': record_id, 'record_type': record_type,
                       'processing_time_ms': processing_time})
    
    return {"status": "success", "message": "记录已删除"}


@router.get("/download/{record_id}")
async def download_file(
    record_id: int, 
    request: Request,
    db: Session = Depends(get_db)
):
    """
    下载文件，需要会话验证
    """
    # 开始计时
    start_time = time.time()
    
    # 记录下载请求
    logger.info(f"下载请求: 记录ID={record_id}",
                extra={'event': 'file_download_request', 'record_id': record_id,
                       'user_id': request.cookies.get('user_uuid', 'unknown')})
    
    try:
        # 手动进行会话验证，确保异常处理正确
        from fastapi.responses import RedirectResponse
        cookie_value = request.cookies.get("room_session")
        if not cookie_value:
            logger.warning("未找到会话cookie，重定向到登录页", extra={'event': 'session_missing', 'record_id': record_id})
            return RedirectResponse(url="/login")
        
        from app import serializer
        try:
            room_id = serializer.loads(cookie_value, max_age=3600)  # 1小时过期
        except:
            logger.warning("无效的会话cookie，重定向到登录页", extra={'event': 'invalid_session', 'record_id': record_id})
            return RedirectResponse(url="/login")
        
        # 验证文件存在和访问权限
        record = db.query(Record).filter(Record.id == record_id).first()
        if not record:
            raise HTTPException(status_code=404, detail="记录不存在")
        
        # 验证用户是否有权访问该房间的文件
        # 确保比较时都是字符串格式
        record_room_id_str = str(record.room_id).zfill(6) if isinstance(record.room_id, int) else record.room_id
        if record_room_id_str != room_id:
            raise HTTPException(status_code=403, detail="无权访问此文件")
        
        if record.type != "file":
            raise HTTPException(status_code=400, detail="该记录不是文件")
        
        uploads_path = get_upload_path()
        file_path = os.path.join(uploads_path, record.content)
        
        if not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="文件不存在")
        
        # 记录文件下载成功
        formatted_room_id = str(record.room_id).zfill(6) if isinstance(record.room_id, int) else record.room_id
        user_id = request.cookies.get('user_uuid', 'unknown')
        logger.info(f"下载成功: {record.original_filename}", extra={'user_id': user_id, 'event': 'download_file', 'room_id': formatted_room_id, 'filename': record.original_filename, 'record_id': record_id})
        
        # 计算总处理时间
        total_processing_time = (time.time() - start_time) * 1000  # 转换为毫秒
        
        # 记录下载开始信息
        logger.info(f"文件下载完成: {formatted_room_id}房间, 记录ID{record_id}, 耗时  {total_processing_time:.2f}ms",
                    extra={'event': 'file_download_started', 'room_id': formatted_room_id,
                           'record_id': record_id, 'filename': record.original_filename,
                           'processing_time_ms': total_processing_time})
        
        # 返回文件
        from fastapi.responses import FileResponse
        return FileResponse(
            path=file_path,
            filename=record.original_filename,
            media_type="application/octet-stream"
        )
    except HTTPException:
        # 重新抛出其他HTTP异常，让全局异常处理器处理
        raise
    except Exception as e:
        # 计算异常时的处理时间
        error_processing_time = (time.time() - start_time) * 1000  # 转换为毫秒
        
        # 记录其他异常
        formatted_room_id = str(record.room_id).zfill(6) if 'record' in locals() else '000000'
        user_id = request.cookies.get('user_uuid', 'unknown')
        logger.error(f"文件下载出错: {str(e)}, 耗时: {error_processing_time:.2f}ms", 
                    extra={'user_id': user_id, 'event': 'download_error', 'record_id': record_id, 
                           'room_id': formatted_room_id, 'error_type': type(e).__name__, 
                           'processing_time_ms': error_processing_time})
        from fastapi.responses import RedirectResponse
        # 出现任何其他异常，重定向到登录页
        return RedirectResponse(url="/login")


@router.get("/config")
async def get_config():
    """
    获取配置信息，包括文件大小限制等
    """
    # 加载配置
    config = load_config()
    
    # 获取最大文件大小限制
    max_file_size_mb = config["upload"].get("max_file_size", 50)  # 默认50MB
    
    return {
        "max_file_size_mb": max_file_size_mb,
        "max_file_size_bytes": max_file_size_mb * 1024 * 1024
    }