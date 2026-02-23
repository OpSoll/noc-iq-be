from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_NAME: str = "NOCIQ API"
    VERSION: str = "1.0.0"
    DATABASE_URL: str = "postgresql://postgres:password@localhost:5432/nociq"

    class Config:
        env_file = ".env"


settings = Settings()
