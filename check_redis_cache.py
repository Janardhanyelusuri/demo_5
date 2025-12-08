#!/usr/bin/env python3
"""
Diagnostic script to check Redis cache status
"""
import asyncio
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.path.dirname(__file__), 'backend'))

from app.core.llm_cache_utils import (
    get_redis_client,
    generate_cache_hash_key,
    get_cached_result
)
from datetime import date


async def check_redis_status():
    """Check Redis connection and cache keys"""
    print("=" * 70)
    print("🔍 REDIS CACHE DIAGNOSTIC")
    print("=" * 70)

    try:
        # Connect to Redis
        client = await get_redis_client()
        print("\n✅ Redis connection successful")

        # Get Redis info
        info = await client.info('server')
        print(f"   Redis version: {info.get('redis_version', 'Unknown')}")
        print(f"   Uptime (seconds): {info.get('uptime_in_seconds', 'Unknown')}")

        # Count all llm_cache keys
        cache_keys = []
        async for key in client.scan_iter(match="llm_cache:*", count=100):
            cache_keys.append(key)

        print(f"\n📊 Total llm_cache keys found: {len(cache_keys)}")

        if cache_keys:
            print("\n📋 Sample of cached keys:")
            for i, key in enumerate(cache_keys[:10]):
                key_str = key.decode('utf-8') if isinstance(key, bytes) else str(key)
                ttl = await client.ttl(key)
                print(f"   {i+1}. {key_str[:50]}... (TTL: {ttl}s)")

        # Test specific hash key from user's logs
        print("\n" + "=" * 70)
        print("🎯 TESTING SPECIFIC HASH KEY FROM LOGS")
        print("=" * 70)

        # Generate the same hash key that should have been cached
        # Based on user's logs: last_week preset for public IP
        # Let's calculate for today's UTC date
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # Last week dates
        start_date = (today - timedelta(days=7)).date()
        end_date = today.date()

        print(f"\nCalculated dates (UTC):")
        print(f"   Start: {start_date}")
        print(f"   End: {end_date}")

        # Test for Azure Public IP (common from logs)
        hash_key = generate_cache_hash_key(
            cloud_platform="azure",
            schema_name="5",  # From user's logs: project 5
            resource_type="publicip",
            start_date=start_date,
            end_date=end_date,
            resource_id=None
        )

        print(f"\nGenerated hash key: {hash_key}")

        # Check if it exists
        cached_result = await get_cached_result(hash_key)

        if cached_result:
            print(f"✅ Cache HIT - Found {len(cached_result)} resources")
        else:
            print("❌ Cache MISS - Key not found in Redis")

            # Check if the key exists but has no value
            exists = await client.exists(hash_key)
            if exists:
                print("   ⚠️ Key exists but returned None (corrupted data?)")
            else:
                print("   Key does not exist in Redis")

        await client.close()

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1

    print("\n" + "=" * 70)
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(check_redis_status())
    exit(exit_code)
