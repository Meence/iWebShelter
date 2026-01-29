from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from ..utils.logger import get_logger
from ..utils.helpers import load_config
import datetime
import asyncio

router = APIRouter()
logger = get_logger(__name__)

# 加载配置
config = load_config()

# WebSocket连接管理器
class ConnectionManager:
    def __init__(self):
        # 存储房间号到连接列表的映射 {room_id: [websocket1, websocket2, ...]}
        self.active_connections: dict[str, list[WebSocket]] = {}
        # 存储每个WebSocket连接对应的client_id {websocket: client_id}
        self.client_ids: dict[WebSocket, str] = {}
        # 存储每个WebSocket连接的建立时间 {websocket: timestamp}
        self.connection_times: dict[WebSocket, float] = {}
        # 存储每个房间每个client_id的最后连接时间 {room_id: {client_id: timestamp}}
        self.last_connection_times: dict[int, dict[str, float]] = {}
        # 存储每个房间每个client_id的最后刷新时间 {room_id: {client_id: timestamp}}
        self.last_refresh_times: dict[int, dict[str, float]] = {}
        # 存储每个房间每个client_id的最后活动时间 {room_id: {client_id: timestamp}}
        self.last_activity_times: dict[int, dict[str, float]] = {}
        # 安全房间ID列表
        self.safe_room_ids = config["server"].get("safe_room_ids", [])
    
    async def connect(self, websocket: WebSocket, room_id: str):
        # 标准化房间号
        normalized_room_id = self.normalize_room_id(room_id)
        logger.debug(f"开始建立WebSocket连接: 房间 {normalized_room_id}", extra={'event': 'websocket_connect_start', 'room_id': normalized_room_id})
        await websocket.accept()
        if normalized_room_id not in self.active_connections:
            self.active_connections[normalized_room_id] = []
            self.last_connection_times[normalized_room_id] = {}
            self.last_refresh_times[normalized_room_id] = {}
            logger.debug(f"创建新房间: {normalized_room_id}", extra={'event': 'websocket_room_created', 'room_id': normalized_room_id})
        self.active_connections[normalized_room_id].append(websocket)
        # 初始化client_id为默认值
        self.client_ids[websocket] = "unknown_client"
        # 记录连接建立时间
        self.connection_times[websocket] = datetime.datetime.now().timestamp()
        logger.debug(f"WebSocket连接已接受: 房间 {normalized_room_id}, 当前房间连接数: {len(self.active_connections[normalized_room_id])}", 
                   extra={'event': 'websocket_connect_accepted', 'room_id': normalized_room_id, 'connection_count': len(self.active_connections[normalized_room_id])})
    
    def disconnect(self, websocket: WebSocket, room_id: str):
        # 标准化房间号
        normalized_room_id = self.normalize_room_id(room_id)
        logger.debug(f"开始断开WebSocket连接: 房间 {normalized_room_id}", extra={'event': 'websocket_disconnect_start', 'room_id': normalized_room_id})
        
        # 获取client_id
        client_id = self.client_ids.get(websocket, "unknown_client")
        
        # 检查连接是否已经被断开（避免重复记录日志）
        is_already_disconnected = websocket not in self.connection_times
        
        # 计算连接持续时间
        connection_start = self.connection_times.get(websocket, 0)
        connection_end = datetime.datetime.now().timestamp()
        duration = connection_end - connection_start
        
        # 清理连接时间映射
        if websocket in self.connection_times:
            del self.connection_times[websocket]
        
        # 从连接列表中移除
        if normalized_room_id in self.active_connections and websocket in self.active_connections[normalized_room_id]:
            self.active_connections[normalized_room_id].remove(websocket)
            logger.debug(f"从房间 {normalized_room_id} 移除连接: 客户端 {client_id}", extra={'event': 'websocket_connection_removed', 'room_id': normalized_room_id, 'client_id': client_id})
            
            # 如果房间为空，清理相关数据
            if not self.active_connections[normalized_room_id]:
                if normalized_room_id in self.active_connections:
                    del self.active_connections[normalized_room_id]
                if normalized_room_id in self.last_connection_times:
                    del self.last_connection_times[normalized_room_id]
                if normalized_room_id in self.last_refresh_times:
                    del self.last_refresh_times[normalized_room_id]
                logger.debug(f"清理空房间: {normalized_room_id}", extra={'event': 'websocket_room_cleaned', 'room_id': normalized_room_id})
            else:
                # 记录最后断开时间（仅当房间仍存在时）
                if client_id != "unknown_client" and normalized_room_id in self.last_connection_times:
                    self.last_connection_times[normalized_room_id][client_id] = connection_end
                logger.debug(f"房间 {normalized_room_id} 剩余连接数: {len(self.active_connections[normalized_room_id])}", 
                           extra={'event': 'websocket_room_connections_updated', 'room_id': normalized_room_id, 'remaining_count': len(self.active_connections[normalized_room_id])})
        
        # 清理client_id映射
        if websocket in self.client_ids:
            del self.client_ids[websocket]
        
        # 只记录持续时间超过5秒的连接断开，并且只记录一次
        # 过滤掉刷新导致的快速断开，减少日志数量
        # 延长阈值，避免用户短暂离开也被记录
        if duration > 5 and not is_already_disconnected:
            # 在安全房间中隐藏真实client_id
            # 确保比较时都是字符串格式
                safe_rooms_str = [str(room).zfill(6) if isinstance(room, int) else room for room in self.safe_room_ids]
                log_client_id = "匿名" if normalized_room_id in safe_rooms_str else client_id
                # 确保输出的房间号是6位格式
                formatted_room_id = normalized_room_id.zfill(6) if normalized_room_id.isdigit() else normalized_room_id
                logger.info(f"WebSocket连接已断开，持续时间: {duration:.2f}秒", extra={'client_id': log_client_id, 'event': 'disconnect', 'room_id': formatted_room_id, 'duration': duration})
    
    async def broadcast(self, room_id: str, message: dict):
        # 标准化房间号
        normalized_room_id = self.normalize_room_id(room_id)
        # 添加消息内容摘要，避免过长日志
        message_type = message.get('type', 'unknown')
        # 根据消息类型添加适当的内容摘要
        content_summary = ''
        if message_type == 'text':
            content_summary = message.get('content', '')[:50] + ('...' if len(message.get('content', '')) > 50 else '')
        elif message_type == 'file':
            content_summary = message.get('filename', 'unknown_file')
        
        # 移除准备广播消息的日志记录以减少信息过载
        
        if normalized_room_id in self.active_connections:
            # 移除连接数日志记录以减少信息过载
            
            successful_sends = 0
            failed_sends = 0
            
            for connection in self.active_connections[normalized_room_id]:
                current_client_id = self.client_ids.get(connection, "unknown_client")
                
                try:
                    # 在安全房间中隐藏真实client_id
                    if normalized_room_id in self.safe_room_ids and 'client_id' in message:
                        message_copy = message.copy()
                        message_copy['client_id'] = "匿名"
                        await connection.send_json(message_copy)
                    else:
                        await connection.send_json(message)
                    successful_sends += 1
                    # 移除单条消息发送成功的日志记录以减少信息过载
                except Exception as e:
                    error_type = type(e).__name__
                    logger.error(f"向客户端 {current_client_id} 广播消息失败: {str(e)} ({error_type})", 
                               extra={'event': 'websocket_broadcast_error', 'client_id': current_client_id, 
                                      'error': str(e), 'error_type': error_type, 'room_id': normalized_room_id, 'message_type': message_type})
                    failed_sends += 1
            
            # 记录广播完成的详细信息
            total_connections = len(self.active_connections[normalized_room_id])
            success_rate = (successful_sends / total_connections * 100) if total_connections > 0 else 0
            # 移除消息广播完成日志记录以减少信息过载
        else:
            logger.debug(f"尝试向不存在的房间 {normalized_room_id} 广播消息", extra={'event': 'websocket_broadcast_room_not_found', 'room_id': normalized_room_id})
    
    def normalize_room_id(self, room_id):
        """标准化房间号为6位字符串格式"""
        if isinstance(room_id, int):
            return str(room_id).zfill(6)
        elif isinstance(room_id, str):
            return room_id.zfill(6) if room_id.isdigit() else room_id
        return str(room_id).zfill(6) if str(room_id).isdigit() else str(room_id)
    
    def update_client_id(self, websocket: WebSocket, client_id: str, room_id: str):
        # 标准化房间号
        normalized_room_id = self.normalize_room_id(room_id)
        # 更新client_id映射（内部仍然保存真实ID以维持连接，但在广播和日志中隐藏）
        self.client_ids[websocket] = client_id
        # 检查是否是刷新导致的快速重连
        current_time = datetime.datetime.now().timestamp()
        last_connection = self.last_connection_times.get(normalized_room_id, {}).get(client_id, 0)
        reconnect_time = current_time - last_connection
        
        # 获取最后刷新时间
        last_refresh = self.last_refresh_times.get(normalized_room_id, {}).get(client_id, 0)
        since_last_refresh = current_time - last_refresh
        

        
        # 在安全房间中隐藏真实client_id
        # 确保比较时都是字符串格式
        safe_rooms_str = [str(room).zfill(6) if isinstance(room, int) else room for room in self.safe_room_ids]
        log_client_id = "匿名" if normalized_room_id in safe_rooms_str else client_id
        
        # 如果是在5秒内重连，认为是页面刷新
        if reconnect_time > 0 and reconnect_time < 5:
            # 避免短时间内重复记录刷新事件（10秒内只记录一次）
            if since_last_refresh > 10:
                # 确保输出的房间号是6位格式
                formatted_room_id = normalized_room_id.zfill(6) if normalized_room_id.isdigit() else normalized_room_id
                logger.info(f"页面刷新", extra={'client_id': log_client_id, 'event': 'refresh', 'room_id': formatted_room_id})
                self.last_refresh_times[normalized_room_id][client_id] = current_time
        else:
            # 正常连接
            # 确保输出的房间号是6位格式
            formatted_room_id = normalized_room_id.zfill(6) if normalized_room_id.isdigit() else normalized_room_id
            logger.info("WebSocket连接已建立", extra={'client_id': log_client_id, 'event': 'connect', 'room_id': formatted_room_id})
        
        # 更新最后连接时间
        self.last_connection_times[normalized_room_id][client_id] = current_time
        # 更新最后活动时间
        self.last_activity_times[normalized_room_id] = self.last_activity_times.get(normalized_room_id, {})
        self.last_activity_times[normalized_room_id][client_id] = current_time
    
    def update_activity_time(self, room_id: str, client_id: str):
        """更新客户端的最后活动时间"""
        # 标准化房间号
        normalized_room_id = self.normalize_room_id(room_id)
        current_time = datetime.datetime.now().timestamp()
        self.last_activity_times[normalized_room_id] = self.last_activity_times.get(normalized_room_id, {})
        self.last_activity_times[normalized_room_id][client_id] = current_time
    
    async def check_timeout(self):
        """检查会话超时并向超时的客户端发送退出信号"""
        while True:
            try:
                current_time = datetime.datetime.now().timestamp()
                timeout_seconds = 3600  # 1小时超时，与auth.py中的cookie过期时间保持一致
                
                # 遍历所有房间的客户端
                rooms_to_check = list(self.last_activity_times.keys())
                for room_id in rooms_to_check:
                    # 确保房间仍然存在
                    if room_id not in self.last_activity_times:
                        continue
                    
                    clients_to_check = list(self.last_activity_times[room_id].keys())
                    for client_id in clients_to_check:
                        try:
                            last_activity = self.last_activity_times[room_id].get(client_id, 0)
                            # 检查是否超时
                            if current_time - last_activity > timeout_seconds:
                                logger.debug(f"检测到会话超时: 房间 {room_id}, 客户端 {client_id}", 
                                           extra={'event': 'session_timeout_detected', 'room_id': room_id, 'client_id': client_id})
                                
                                # 查找对应的WebSocket连接
                                websocket_to_remove = None
                                for websocket, conn_client_id in self.client_ids.items():
                                    if conn_client_id == client_id and room_id in self.active_connections and websocket in self.active_connections[room_id]:
                                        websocket_to_remove = websocket
                                        break
                                
                                if websocket_to_remove:
                                    try:
                                        # 发送超时退出信号
                                        await websocket_to_remove.send_json({
                                            'type': 'session_timeout',
                                            'message': '会话已超时，请重新登录'
                                        })
                                        # 记录日志
                                        # 确保比较时都是字符串格式
                                        safe_rooms_str = [str(room).zfill(6) if isinstance(room, int) else room for room in self.safe_room_ids]
                                        log_client_id = "匿名" if str(room_id) in safe_rooms_str else client_id
                                        # 确保输出的房间号是6位格式
                                        formatted_room_id = str(room_id).zfill(6) if str(room_id).isdigit() else str(room_id)
                                        logger.info("会话超时，已通知客户端退出", 
                                                  extra={'client_id': log_client_id, 'event': 'session_timeout', 'room_id': formatted_room_id})
                                    except Exception as send_error:
                                        logger.error(f"向超时客户端发送消息失败: {str(send_error)}", 
                                                  extra={'event': 'timeout_message_send_error', 'error': str(send_error)})
                                    finally:
                                        # 清理活动时间记录
                                        if room_id in self.last_activity_times and client_id in self.last_activity_times[room_id]:
                                            del self.last_activity_times[room_id][client_id]
                                        # 断开连接
                                        self.disconnect(websocket_to_remove, room_id)
                                else:
                                    # 如果找不到WebSocket连接，仍然清理活动时间记录
                                    if room_id in self.last_activity_times and client_id in self.last_activity_times[room_id]:
                                        del self.last_activity_times[room_id][client_id]
                        except Exception as client_error:
                            logger.error(f"处理客户端超时检查时出错: {str(client_error)}", 
                                      extra={'event': 'client_timeout_check_error', 'error': str(client_error)})
                            # 继续处理下一个客户端
                            continue
                
                # 清理空的房间记录
                for room_id in list(self.last_activity_times.keys()):
                    if not self.last_activity_times[room_id]:
                        del self.last_activity_times[room_id]
                        logger.debug(f"清理空房间活动记录: {room_id}", extra={'event': 'empty_room_cleanup', 'room_id': room_id})
            
            except Exception as e:
                logger.error(f"检查会话超时出错: {str(e)}", extra={'event': 'timeout_check_error'})
            
            # 每分钟检查一次
            await asyncio.sleep(60)

# 创建连接管理器实例
manager = ConnectionManager()

# 全局标志，确保只启动一次超时检查任务
timeout_check_started = False

# 启动会话超时检查任务
async def start_timeout_check():
    global timeout_check_started
    if not timeout_check_started:
        asyncio.create_task(manager.check_timeout())
        timeout_check_started = True
        logger.debug("会话超时检查任务已启动", extra={'event': 'timeout_check_task_started'})

@router.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str):
    """
    WebSocket端点，处理实时通信
    """
    logger.debug(f"收到WebSocket连接请求，房间号: {room_id}", 
               extra={"event": "websocket_connection_request", "room_id": room_id})
    
    # 验证房间号格式
    if not room_id.isdigit() or len(room_id) != 6:
        logger.warning(f"WebSocket连接失败：无效的房间号格式: {room_id}", 
                      extra={"event": "websocket_invalid_room_id", "room_id": room_id})
        await websocket.close(code=4001, reason="Invalid room ID format")
        return
    
    # 确保房间号格式一致（6位数字）
    room_id = room_id.zfill(6)
    logger.debug(f"标准化房间号为: {room_id}", 
                extra={"event": "websocket_room_id_normalized", "room_id": room_id})
    
    # 从cookie验证会话（通过WebSocket的HTTP连接）
    try:
        # 从WebSocket的HTTP连接获取cookies
        cookies = websocket.headers.get("cookie", "")
        logger.debug(f"WebSocket请求中的Cookie: {cookies}", 
                    extra={"event": "websocket_cookie_check"})
        
        # 模拟Request对象以使用现有的认证逻辑
        class MockRequest:
            def __init__(self, cookies_str):
                # 解析cookie字符串
                self.cookies = {}
                if cookies_str:
                    for cookie in cookies_str.split(';'):
                        if '=' in cookie:
                            key, value = cookie.strip().split('=', 1)
                            self.cookies[key] = value
        
        mock_request = MockRequest(cookies)
        
        # 使用现有的认证函数验证会话
        from src.api.auth import get_room_id_from_cookie
        auth_room_id = get_room_id_from_cookie(mock_request)
        
        # 验证请求的房间号与会话中的房间号匹配
        if auth_room_id != room_id:
            logger.warning(f"WebSocket连接失败：房间号不匹配，请求: {room_id}, 会话: {auth_room_id}",
                         extra={"event": "websocket_room_id_mismatch", "requested_room_id": room_id, 
                                "session_room_id": auth_room_id})
            await websocket.close(code=4003, reason="Room ID mismatch")
            return
        
        logger.debug(f"WebSocket认证成功: 房间 {room_id}", 
                           extra={"event": "websocket_auth_success", "room_id": room_id})
    except HTTPException as e:
        logger.warning(f"WebSocket认证失败: {str(e.detail)}", 
                      extra={"event": "websocket_auth_failed", "error": str(e.detail)})
        await websocket.close(code=4002, reason="Authentication required")
        return
    except Exception as e:
        logger.error(f"WebSocket认证过程中出错: {str(e)}", 
                    extra={"event": "websocket_auth_exception", "error": str(e)})
        await websocket.close(code=4000, reason="Authentication error")
        return
    
    # 连接到房间
    await manager.connect(websocket, room_id)
    
    # 发送初始连接确认消息
    await websocket.send_json({"type": "connection_established", "room_id": room_id})
    
    try:
        # 确保超时检查任务已启动
        await start_timeout_check()
        
        while True:
            # 接收消息
            data = await websocket.receive_json()
            # 从接收到消息后开始计算处理时间
            start_time = datetime.datetime.now()
            
            message_type = data.get('type')
            # 根据消息类型添加内容摘要
            msg_content_summary = ''
            if message_type == 'text':
                msg_content_summary = data.get('content', '')[:50] + ('...' if len(data.get('content', '')) > 50 else '')
            elif message_type == 'file':
                msg_content_summary = data.get('filename', 'unknown_file')
            
            client_id = manager.client_ids.get(websocket, "System")
            
            # 处理注册client_id的消息
            if data['type'] == 'register_client':
                client_id = data['client_id']
                # 使用客户端发送的room_id或当前房间ID，并确保为字符串格式
                register_room_id_raw = data.get('room_id', room_id)
                register_room_id = str(register_room_id_raw).zfill(6) if isinstance(register_room_id_raw, int) else register_room_id_raw
                
                # 在安全房间中隐藏真实client_id
                safe_rooms_str = [str(r).zfill(6) if isinstance(r, int) else r for r in manager.safe_room_ids]
                log_client_id = "匿名" if room_id in safe_rooms_str else client_id
                
                manager.update_client_id(websocket, client_id, register_room_id)
                # 更新活动时间
                manager.update_activity_time(room_id, client_id)
                logger.info(f"客户端注册成功", 
                           extra={"event": "websocket_client_registered", "client_id": log_client_id, 
                                  "room_id": room_id, "register_room_id": register_room_id})
            # 处理ping消息
            elif data['type'] == 'ping':
                # 响应ping消息以保持连接活跃
                await websocket.send_json({"type": "pong", "timestamp": data.get("timestamp")})
                # 更新活动时间
                client_id = manager.client_ids.get(websocket, "unknown_client")
                if client_id != "unknown_client":
                    manager.update_activity_time(room_id, client_id)
                logger.debug(f"响应ping消息来自房间 {room_id}", 
                            extra={"event": "websocket_ping_response", "room_id": room_id})
            else:
                # 更新客户端活动时间
                client_id = manager.client_ids.get(websocket, "unknown_client")
                if client_id != "unknown_client":
                    manager.update_activity_time(room_id, client_id)
                
                # 广播其他消息到同一房间
            try:
                start_broadcast_time = datetime.datetime.now()
                await manager.broadcast(room_id, data)
                broadcast_time = (datetime.datetime.now() - start_broadcast_time).total_seconds() * 1000  # 毫秒
                
                # 计算处理时间（包括处理和广播）
                processing_time = (datetime.datetime.now() - start_time).total_seconds() * 1000  # 毫秒
                logger.debug(f"消息广播处理完成，用时: {broadcast_time:.2f}ms", 
                           extra={"event": "websocket_broadcast_processed", "room_id": room_id,
                                  "message_type": message_type, "broadcast_time_ms": broadcast_time})
                
                # 记录收到消息的处理时间
                logger.info(f"收到消息：类型 {message_type}", 
                           extra={"event": "websocket_message_received", "room_id": room_id, 
                                  "message_type": message_type, "content_summary": msg_content_summary,
                                  "client_id": client_id, "processing_time_ms": processing_time})
            except Exception as broadcast_error:
                error_type = type(broadcast_error).__name__
                # 计算处理时间（包括处理和异常处理）
                processing_time = (datetime.datetime.now() - start_time).total_seconds() * 1000  # 毫秒
                logger.error(f"广播消息时出错: {str(broadcast_error)} ({error_type})", 
                           extra={"event": "websocket_broadcast_exception", "room_id": room_id,
                                  "message_type": message_type, "error": str(broadcast_error), 
                                  "error_type": error_type, "client_id": client_id, "processing_time_ms": processing_time})
                
                # 记录收到消息的处理时间（即使出错）
                logger.info(f"收到消息：类型 {message_type}", 
                           extra={"event": "websocket_message_received", "room_id": room_id, 
                                  "message_type": message_type, "content_summary": msg_content_summary,
                                  "client_id": client_id, "processing_time_ms": processing_time})
    except WebSocketDisconnect:
        # 断开连接
        logger.debug(f"WebSocket正常断开: 房间 {room_id}", 
                   extra={"event": "websocket_normal_disconnect", "room_id": room_id})
        manager.disconnect(websocket, room_id)
    except Exception as e:
        # 获取client_id
        client_id = manager.client_ids.get(websocket, "unknown_client")
        # 确保输出的房间号是6位格式
        formatted_room_id = room_id.zfill(6) if room_id.isdigit() else room_id
        logger.error(f"WebSocket错误: {str(e)}", extra={'client_id': client_id, 'event': 'error', 'room_id': formatted_room_id})
        manager.disconnect(websocket, room_id)
