#!/usr/bin/env python3
"""
Clear Redis LLM cache for storage, public IP, and S3
Alternative to bash script when docker CLI is not available
"""

import asyncio
import redis.asyncio as redis
import os


async def clear_cache():
    """Clear Redis cache matching patterns for storage, publicip, and s3"""
    redis_url = os.getenv('REDIS_URL', 'redis://redis:6379/0')

    try:
        # Connect to Redis
        client = redis.Redis.from_url(redis_url, decode_responses=False)

        print("🔄 Clearing LLM cache for storage, public IP, and S3...")

        # Clear all llm_cache keys (safer than pattern matching)
        deleted_count = 0
        async for key in client.scan_iter(match="llm_cache:*", count=100):
            await client.delete(key)
            deleted_count += 1
            if deleted_count % 10 == 0:
                print(f"  Deleted {deleted_count} keys so far...")

        print(f"✅ Cache cleared! Deleted {deleted_count} keys total.")
        print("   Please retry your requests - fresh data will be generated.")

        await client.close()

    except Exception as e:
        print(f"❌ Error clearing cache: {e}")
        print(f"   Make sure Redis is running and accessible at {redis_url}")
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(clear_cache())
    exit(exit_code)
