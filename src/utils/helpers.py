import os
import secrets
import datetime
from ruamel.yaml import YAML

# 创建YAML对象实例，用于处理配置文件，保留注释
yaml = YAML()
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)

# 读取配置
def load_config():
    # 使用绝对路径读取配置文件，确保从项目根目录读取
    current_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(current_dir, "..", "..", "config.yaml")
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.load(f)

# 保存配置
def save_config(config):
    # 使用绝对路径保存配置文件，确保保存到项目根目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(current_dir, "..", "..", "config.yaml")
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(config, f)

# 生成或获取密钥
def get_secret_key():
    config = load_config()
    if not config["server"].get('secret_key'):
        config["server"]['secret_key'] = secrets.token_urlsafe(32)
        save_config(config)
    return config["server"]['secret_key']

# 生成记录索引
def generate_record_index(room_id):
    import random
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")[:-3]
    random_suffix = random.randint(100, 999)  # 三位随机数，进一步降低冲突概率
    return f"{room_id}{timestamp}{random_suffix}"

# 获取上传文件保存路径
def get_upload_path():
    config = load_config()
    return config["upload"]["uploads_path"]

# 获取文件格式图标库路径
def get_file_icons_path():
    config = load_config()
    return config["icons"].get("file_icons_path", "file_icons")

# 创建日期目录（旧版本，保留以确保兼容性）
def create_date_directory():
    # 为了向后兼容，仍然返回原来格式的目录
    return create_directory_by_type(is_persistent=False)

# 创建目录结构（支持年/月/日层级和持久化分类）
def create_directory_by_type(is_persistent=False, specific_date=None):
    """
    创建目录结构，支持持久化/非持久化分类和年/月/日层级结构
    
    参数：
    is_persistent: bool - 是否为持久化文件目录
    specific_date: datetime - 可选，指定日期，默认使用当前日期
    
    返回：
    str - 创建的目录路径
    """
    upload_path = get_upload_path()
    
    # 确定基础目录
    base_dir = "persistent" if is_persistent else "daily"
    
    # 获取日期
    if specific_date is None:
        specific_date = datetime.datetime.now()
    
    # 创建年/月/日层级目录
    year = specific_date.strftime("%Y")
    month = specific_date.strftime("%m")
    day = specific_date.strftime("%d")
    
    # 构建完整路径
    full_path = os.path.join(upload_path, base_dir, year, month, day)
    
    # 递归创建目录
    os.makedirs(full_path, exist_ok=True)
    
    return full_path

# 处理文件名冲突
def handle_filename_conflict(directory, filename):
    base, ext = os.path.splitext(filename)
    counter = 1
    new_filename = filename
    
    while os.path.exists(os.path.join(directory, new_filename)):
        new_filename = f"{base} ({counter}){ext}"
        counter += 1
    
    return new_filename

# 格式化文件大小
def format_file_size(size_in_bytes):
    if size_in_bytes < 1024:
        return f"{size_in_bytes} B"
    elif size_in_bytes < 1024 * 1024:
        return f"{size_in_bytes / 1024:.2f} KB"
    else:
        return f"{size_in_bytes / (1024 * 1024):.2f} MB"

