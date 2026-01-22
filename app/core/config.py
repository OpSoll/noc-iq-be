from pydantic import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "NOCIQ API"
    VERSION: str = "1.0.0"

settings = Settings()
