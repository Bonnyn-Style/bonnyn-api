from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

SQLALCHEMY_DATABASE_URL = "sqlite:///./app_belleza.db"

# Configuramos el motor con timeout de 30 segundos y check_same_thread=False
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    connect_args={"check_same_thread": False, "timeout": 30}
)

# Habilitamos el modo WAL (Write-Ahead Logging) y PRAGMA synchronous
# para permitir lecturas y escrituras concurrentes sin errores de "database is locked".
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Generador de sesiones para las rutas HTTP de FastAPI (Inyección de dependencias)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()