from sqlalchemy import insert
import logging

logger = logging.getLogger("perf")

def bulk_insert_records(db_session, model_class, records_list):
    """Uses SQLAlchemy Core insert() for high-performance bulk insertions."""
    if not records_list:
        return
    stmt = insert(model_class).values(records_list)
    db_session.execute(stmt)
    db_session.commit()
    logger.info(f"Bulk inserted {len(records_list)} records successfully.")

async def verify_redis_eviction_policy(redis_client):
    """Queries Redis configurations to assert memory eviction is allkeys-lru."""
    try:
        config = await redis_client.config_get("maxmemory-policy")
        policy = config.get("maxmemory-policy", "unknown")
        if policy != "allkeys-lru":
            logger.warning(f"Non-recommended Redis eviction policy detected: {policy}")
        return policy
    except Exception as e:
        logger.error(f"Failed to query Redis eviction policy: {e}")
        return "error"
