import logging
import os
from datetime import datetime

# ANSI 颜色代码
class Colors:
    RESET = "\033[0m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

# 全局标志，确保日志配置只执行一次
_logging_configured = False

# 创建日志目录
log_dir = "logs"
os.makedirs(log_dir, exist_ok=True)

# 创建日志文件
log_file = os.path.join(log_dir, f"iwebshelter_{datetime.now().strftime('%Y%m%d')}.log")

# 自定义Formatter，添加颜色和改进时间格式
class CustomFormatter(logging.Formatter):
    # 根据日志级别设置不同颜色
    COLORS = {
        logging.DEBUG: Colors.BLUE,
        logging.INFO: Colors.GREEN,
        logging.WARNING: Colors.YELLOW,
        logging.ERROR: Colors.RED,
        logging.CRITICAL: Colors.MAGENTA
    }
    
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created)
        return dt.strftime("%Y-%m-%d %H:%M:%S.") + f"{int(record.msecs):03d}"
    
    def format(self, record):
        # 保存原始格式
        original_format = self._fmt
        
        # 检查是否是控制台输出（stream handler）
        is_console = hasattr(record, '_stream_handler') and record._stream_handler
        
        if is_console:
            # 如果是控制台输出，使用更简洁的格式并添加颜色
            color = self.COLORS.get(record.levelno, Colors.WHITE)
            # 简洁格式，只显示消息内容
            self._fmt = f"{color}%(message)s{Colors.RESET}"
        
        # 调用父类方法进行格式化
        result = super().format(record)
        
        # 恢复原始格式
        self._fmt = original_format
        return result

# 自定义Adapter，为日志记录添加user_id、event和room_id等字段
class ContextAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        # 合并self.extra和kwargs中的extra
        extra = self.extra.copy()
        if 'extra' in kwargs:
            extra.update(kwargs['extra'])

        # 获取字段，使用默认值
        user_id = extra.get('user_id', extra.get('client_id', 'unknown'))
        event = extra.get('event', msg)  # 如果没有提供event，使用msg
        room_id = extra.get('room_id', '')

        # 更新kwargs中的extra
        kwargs['extra'] = {
            'user_id': user_id,
            'event': event,
            'room_id': room_id
        }
        # 添加其他可能的字段，注意避免使用与Python logging模块内置字段冲突的名称
        for key in extra:
            # 重命名filename字段为upload_filename以避免冲突
            if key == 'filename':
                kwargs['extra']['upload_filename'] = extra[key]
            # 确保不覆盖其他内置字段
            elif key not in ['filename', 'module', 'lineno', 'pathname', 'funcName']:
                kwargs['extra'][key] = extra[key]
        # 返回原始消息
        return msg, kwargs

# 配置日志函数
def configure_logging():
    global _logging_configured
    if not _logging_configured:
        # 为文件日志和控制台日志设置不同的格式
        # 文件日志格式 - 完整信息
        file_log_format = '%(asctime)s | %(levelname)s | 房间:%(room_id)s | 用户:%(user_id)s | %(message)s'
        
        # 创建文件日志formatter
        file_formatter = CustomFormatter(file_log_format)
        
        # 获取根日志记录器
        root_logger = logging.getLogger()
        # 设置根日志级别为INFO，确保INFO级别的日志能够被记录
        root_logger.setLevel(logging.INFO)
        
        # 检查是否已经有处理器，如果没有则添加
        if not root_logger.handlers:
            # 添加文件处理器 - 设置为INFO级别，记录关键信息
            file_handler = logging.FileHandler(log_file)
            file_handler.setFormatter(file_formatter)
            file_handler.setLevel(logging.INFO)
            root_logger.addHandler(file_handler)
            
            # 添加流处理器 - 同样设置为INFO级别，使用简洁格式
            stream_handler = logging.StreamHandler()
            # 为控制台日志创建单独的formatter
            console_formatter = CustomFormatter('%(message)s')  # 这里的格式不重要，因为会在format方法中覆盖
            stream_handler.setFormatter(console_formatter)
            stream_handler.setLevel(logging.INFO)
            root_logger.addHandler(stream_handler)
            
            # 为流处理器的记录添加标记，以便在格式化时应用颜色
            original_emit = stream_handler.emit
            def emit_with_flag(record):
                record._stream_handler = True
                return original_emit(record)
            stream_handler.emit = emit_with_flag
        
        # 特殊设置：为uvicorn相关日志设置级别
        uvicorn_access_logger = logging.getLogger('uvicorn.access')
        uvicorn_access_logger.setLevel(logging.WARNING)
        
        uvicorn_error_logger = logging.getLogger('uvicorn.error')
        uvicorn_error_logger.setLevel(logging.INFO)
        
        uvicorn_main_logger = logging.getLogger('uvicorn')
        uvicorn_main_logger.setLevel(logging.INFO)
        
        _logging_configured = True

# 初始化日志配置
configure_logging()

# 创建日志记录器
def get_logger(name):
    logger = logging.getLogger(name)
    # 返回Adapter，默认extra为空
    return ContextAdapter(logger, {})

# 创建一个只输出到控制台的日志函数，用于启动信息等不需要记录到文件的内容
def console_only_log(message, level='info'):
    """
    只将日志输出到控制台，不写入文件
    用于启动信息、系统状态等不需要持久化的日志
    """
    # 直接使用print输出启动信息，避免复杂的日志格式化问题
    print(message)
    
    # # 保留原注释，以备将来参考
    # # 获取根日志记录器
    # root_logger = logging.getLogger()
    # 
    # # 创建一个临时的日志记录器，用于调用自定义格式化器
    # temp_logger = logging.getLogger('console_only')
    # 
    # # 创建日志记录
    # record = temp_logger.makeRecord(
    #     name='console_only',
    #     level=getattr(logging, level.upper()),
    #     fn='',
    #     lno=0,
    #     msg=message,
    #     args=(),
    #     exc_info=None
    # )
    # 
    # # 设置必要的字段，避免formatter错误
    # record.room_id = ''
    # record.user_id = 'System'
    # record.event = message
    # 
    # # 标记为控制台日志
    # record._stream_handler = True
    # 
    # # 只发送到流处理器（控制台）
    # for handler in root_logger.handlers:
    #     if isinstance(handler, logging.StreamHandler):
    #         formatter = handler.formatter
    #         if formatter:
    #             print(formatter.format(record))
    #         else:
    #             print(record.getMessage())
