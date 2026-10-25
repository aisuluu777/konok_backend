from redis.asyncio import Redis

from app.config import get_settings

redis_client = Redis.from_url(get_settings().redis_url, decode_responses=True)


def get_redis() -> Redis:
    return redis_client
