from fastapi import APIRouter, HTTPException, Depends, Body
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import time
from datetime import datetime, timezone, timedelta
import uuid
from typing import Dict, Tuple
from itsdangerous import URLSafeTimedSerializer
from ..utils.helpers import load_config, get_secret_key
from ..utils.logger import get_logger

# 读取配置
config = load_config()

# 验证房间号格式
def verify_room_id(room_id: str) -> bool:
    return isinstance(room_id, str) and len(room_id) == 6 and room_id.isdigit()

# 创建会话序列化器
SECRET_KEY = get_secret_key()
serializer = URLSafeTimedSerializer(SECRET_KEY)

router = APIRouter()
logger = get_logger(__name__)

# 内存限流存储
rate_limit_store: Dict[str, Tuple[int, float]] = {}

# 自定义限流依赖
def rate_limiter(request: Request):
    """
    基于内存的速率限制器 - 仅对登录请求进行限流
    """
    # 只对登录请求进行限流检查
    if request.url.path == "/api/login":
        client_ip = request.client.host
        times = config["server"]["login_rate_limit_min"]  # 每分钟最多尝试次数
        seconds = 60  # 固定为每分钟
        
        current_time = time.time()
        
        if client_ip in rate_limit_store:
            count, last_time = rate_limit_store[client_ip]
            if current_time - last_time < seconds:
                if count >= times:
                    # 记录限流事件日志
                    logger.warning(
                        "登录请求被限流",
                        extra={
                            "client_ip": client_ip,
                            "current_count": count,
                            "limit_count": times,
                            "event": "login_rate_limited"
                        }
                    )
                    raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
                else:
                    rate_limit_store[client_ip] = (count + 1, last_time)
            else:
                rate_limit_store[client_ip] = (1, current_time)
        else:
            rate_limit_store[client_ip] = (1, current_time)
    # 对于非登录请求，直接通过，不进行限流检查

# 依赖：验证会话
def get_room_id_from_cookie(request: Request):
    logger.debug("开始身份验证过程", extra={"event": "auth_verification_start"})
    cookie_value = request.cookies.get("room_session")
    logger.debug(f"获取房间会话cookie: {'存在' if cookie_value else '不存在'}", 
                 extra={"event": "auth_cookie_check", "cookie_exists": bool(cookie_value)})
    
    if not cookie_value:
        logger.warning("会话验证失败：未找到房间会话cookie", extra={"event": "auth_failed_no_cookie"})
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    try:
        room_id_raw = serializer.loads(cookie_value, max_age=3600)  # 1小时过期，确保会话有合理的有效期限制
        logger.debug(f"成功反序列化会话cookie，原始房间号: {room_id_raw}", 
                     extra={"event": "auth_cookie_deserialized"})
        
        # 确保房间号是字符串类型且为6位格式
        if isinstance(room_id_raw, int):
            room_id = str(room_id_raw).zfill(6)
        else:
            room_id = str(room_id_raw).zfill(6)
        
        # 验证格式
        if not room_id.isdigit() or len(room_id) != 6:
            logger.warning(f"会话验证失败：无效的房间号格式: {room_id}", 
                           extra={"event": "auth_failed_invalid_format"})
            raise ValueError("无效的房间号格式")
        
        logger.debug(f"会话验证成功，房间号: {room_id}", 
                   extra={"event": "auth_success", "room_id": room_id})
        return room_id
    except Exception as e:
        logger.error(f"会话验证失败：{str(e)}", 
                    extra={"event": "auth_failed_exception", "error": str(e)})
        raise HTTPException(status_code=401, detail="Invalid session")

# 定义登录请求模型
class LoginRequest(BaseModel):
    room_id: str = Field(..., min_length=1, max_length=6, description="6位房间号")

@router.post("/login")
async def login(request: Request, login_data: LoginRequest = Body(...), _: None = Depends(rate_limiter)):
    """
    登录接口，验证房间号并返回成功信息
    """
    # 确保房间号是6位数字格式，自动补零
    room_id = login_data.room_id.zfill(6)
    
    # 验证是否为有效数字
    if not room_id.isdigit():
        logger.warning(f"登录失败：房间号不是数字格式", extra={"event": "login_failed_invalid_format", "room_id": room_id})
        raise HTTPException(status_code=400, detail="房间号必须为数字格式")
    if len(room_id) != 6:
        logger.warning(f"登录失败：房间号不是6位数字", extra={"event": "login_failed_invalid_length", "room_id": room_id})
        raise HTTPException(status_code=400, detail="房间号必须为6位数字")
    
    # 生成会话cookie
    session_value = serializer.dumps(room_id)
    
    # 记录登录成功日志，确保房间号以6位格式显示
    formatted_room_id = room_id.zfill(6)
    # 生成UUID作为用户唯一标识符
    user_uuid = str(uuid.uuid4())
    
    # 先创建响应对象
    response = JSONResponse(content={"status": "success", "room_id": room_id})
    
    # 将用户UUID存储在cookie中，以便后续操作使用 - 设置为会话cookie（关闭浏览器即失效）
    response.set_cookie(
        key="user_uuid",
        value=user_uuid,
        httponly=True,
        secure=False,  # 开发环境下使用False，生产环境应使用True
        samesite="lax",
        max_age=None,  # 不设置持久化时间，仅在会话期间有效
        expires=None,  # 会话cookie，关闭浏览器即失效
        path="/",      # 确保在整个站点生效
        domain=None    # 使用默认域名
    )
    
    # 设置会话cookie - 设置1小时过期时间，与serializer.loads中的过期时间保持一致
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    response.set_cookie(
        key="room_session",
        value=session_value,
        httponly=True,
        secure=False,  # 开发环境下使用False，生产环境应使用True
        samesite="lax",
        max_age=3600,  # 设置1小时过期时间
        expires=expires,  # 设置1小时过期时间（UTC格式）
        path="/",      # 确保在整个站点生效
        domain=None    # 使用默认域名
    )
    
    # 清理任何可能存在的过期cookie（如果有）
    response.delete_cookie("user_uuid_old", path="/")
    response.delete_cookie("room_session_old", path="/")
    
    logger.info(f"用户登录成功", extra={"user_id": user_uuid, "event": "user_login", "room_id": formatted_room_id})
    
    return response