from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from ..utils.helpers import load_config

# 读取配置
config = load_config()

# 获取项目根目录
project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))

# 创建数据库引擎
database_path = os.path.join(project_root, config["database"]["path"])
engine = create_engine(f'sqlite:///{database_path}')
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Record(Base):
    __tablename__ = "records"
    
    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(String(6), index=True)
    record_index = Column(String(26), index=True)
    upload_timestamp = Column(String(20))
    type = Column(String(10))  # 'text' or 'file'
    content = Column(String)
    original_filename = Column(String, nullable=True)
    file_extension = Column(String, nullable=True)
    file_size = Column(Integer, nullable=True)
    client_id = Column(String(50), nullable=True)

# 获取数据库会话
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

