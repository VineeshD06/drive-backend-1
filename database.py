from sqlalchemy import create_engine, make_url
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

from sqlalchemy.engine.url import URL

def get_engine():
    use_unix_socket = os.getenv("USE_UNIX_SOCKET", "false").lower() == "true"

    if use_unix_socket:
        # Cloud Run / Prod using Unix Socket
        db_user = os.environ["DB_USER"]
        db_pass = os.environ["DB_PASS"]
        db_name = os.environ["DB_NAME"]
        unix_socket_path = os.environ["INSTANCE_UNIX_SOCKET"]  # e.g. /cloudsql/...

        return create_engine(
            URL.create(
                drivername="postgresql+psycopg2",  # or mysql+pymysql for MySQL
                username=db_user,
                password=db_pass,
                database=db_name,
                query={"host": unix_socket_path},
            )
        )
    else:
        # Local dev over TCP
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL not set for local dev")
        return create_engine(db_url)

# Global engine
engine = get_engine()

SessionLocal = sessionmaker(bind=engine, autoflush=False)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()