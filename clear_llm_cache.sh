#!/bin/bash
# Clear LLM cache for storage, public IP, and S3

echo "🔄 Clearing LLM cache for storage, public IP, and S3..."

# Find Redis container name
REDIS_CONTAINER=$(docker ps --format '{{.Names}}' | grep redis | head -n 1)

if [ -z "$REDIS_CONTAINER" ]; then
    echo "❌ Redis container not found!"
    exit 1
fi

echo "📦 Found Redis container: $REDIS_CONTAINER"

# Clear cache for storage accounts
echo "🗑️  Clearing storage account cache..."
docker exec -it $REDIS_CONTAINER redis-cli --scan --pattern "llm_cache:*storage*" | xargs -r docker exec -i $REDIS_CONTAINER redis-cli DEL

# Clear cache for public IPs
echo "🗑️  Clearing public IP cache..."
docker exec -it $REDIS_CONTAINER redis-cli --scan --pattern "llm_cache:*publicip*" | xargs -r docker exec -i $REDIS_CONTAINER redis-cli DEL

# Clear cache for S3
echo "🗑️  Clearing S3 cache..."
docker exec -it $REDIS_CONTAINER redis-cli --scan --pattern "llm_cache:*s3*" | xargs -r docker exec -i $REDIS_CONTAINER redis-cli DEL

echo "✅ Cache cleared! Please retry your requests."
