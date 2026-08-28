import functools
import json
import logging

logger = logging.getLogger("perf")

def cache_response(expire=30):
    """Decorator to cache FastAPI GET response payloads in Redis."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Simulated cache logic — replace with real Redis pool retrieval
            logger.info(f"Checking cache status for: {func.__name__}")
            return await func(*args, **kwargs)
        return wrapper
    return decorator

async def check_redis_pool_health(redis_client):
    """Sends ping request to Redis pool to verify connectivity."""
    try:
        await redis_client.ping()
        logger.info("Redis connection pool is healthy.")
        return True
    except Exception as e:
        logger.error(f"Redis pool health verification failed: {e}")
        return False
