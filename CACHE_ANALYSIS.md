# LLM Cache Hash Analysis - Bulk vs Single Resource

## Investigation Summary

This document analyzes the cache hashing mechanism for LLM recommendations and explains why frontend errors occur despite successful backend processing.

## Cache Hash Generation

**Location**: `backend/app/core/llm_cache_utils.py:35-75`

### Hash Key Formula:
```python
cache_string = f"{cloud}|{schema}|{rtype}|{start_str}|{end_str}|{rid}"
hash_key = f"llm_cache:{md5(cache_string).hexdigest()}"
```

### Examples:

**Single Resource:**
- Input: `resource_id = "/subscriptions/.../storageaccounts/foo"`
- Cache string: `"azure|myschema|storage|2024-01-01|2024-01-31|/subscriptions/.../storageaccounts/foo"`
- Result: One unique hash per resource

**Bulk Analysis:**
- Input: `resource_id = None` (or empty string)
- Cache string: `"azure|myschema|storage|2024-01-01|2024-01-31|"`
- Result: **Single hash for ALL resources of that type**

## Response Structure Flow

### Single Resource Request:
1. `run_llm_analysis()` → `run_llm_storage()` → Returns single dict
2. API endpoint wraps it: `result_list = [result] if isinstance(result, dict) else result`
3. Cached as: `[{resource_id, recommendations, ...}]`
4. Frontend receives: JSON string of list with 1 item

### Bulk Resource Request:
1. `run_llm_analysis()` → `run_llm_storage_all_resources()` → Returns list of dicts
2. API endpoint keeps it: `result_list = result` (already a list)
3. Cached as: `[{resource_id: "res1", ...}, {resource_id: "res2", ...}, ...]`
4. Frontend receives: JSON string of list with N items

**Both structures are identical**: Always a list of dicts, always JSON serialized.

## Expected JSON Structure

All LLM responses (VM, Storage, Public IP) follow this format:

```json
{
  "resource_id": "/subscriptions/.../resourceName",
  "_forecast_monthly": 123.45,
  "_forecast_annual": 1481.40,
  "recommendations": {
    "effective_recommendation": {
      "text": "Action description",
      "explanation": "Why + Math calculations",
      "saving_pct": 15.5
    },
    "additional_recommendation": [
      {"text": "...", "explanation": "...", "saving_pct": 10.0}
    ],
    "base_of_recommendations": ["metric1: value", "metric2: value"]
  },
  "cost_forecasting": {
    "monthly": 123.45,
    "annually": 1481.40
  },
  "anomalies": [
    {"metric_name": "CPU", "timestamp": "2024-01-15", "value": 95.5, "reason_short": "Spike detected"}
  ],
  "contract_deal": {
    "assessment": "good|bad|unknown",
    "for_sku": "Standard_LRS",
    "reason": "Analysis explanation",
    "monthly_saving_pct": 5.0,
    "annual_saving_pct": 6.0
  }
}
```

## The Actual Problem: Cache Format Incompatibility

### Recent Structural Changes:

Looking at git history, these commits changed the LLM response format:

- **185a549**: "MAJOR: Azure VM prompt restructuring - LLM-driven analysis"
- **f66223f**: "Azure Storage: Fetch DIVERSE storage options"
- **07841a3**: "Azure Public IP: Fetch DIVERSE IP options"
- **df821be**: "Reduce prompt sizes and fix contract_deal definition"
- **7ac10c1**: "Fix LLM calculation errors: add explicit instructions"

### Evidence from Logs:

```
Request 1: Cache MISS
- Fresh LLM call with NEW format
- Frontend displays correctly ✅

Request 2: Cache HIT
- Returns OLD cached data (from before changes)
- Frontend parsing fails ❌
```

### Why This Happens:

1. **Before code changes**: LLM returned format v1, cached in Redis (TTL: 24h)
2. **After code changes**: LLM returns format v2, but cache still has v1
3. **First request**: Cache miss → Fresh v2 data → Works
4. **Second request**: Cache hit → Old v1 data → Frontend expects v2 → **Parse error**

## Solution

### Option 1: Bash Script (requires docker CLI)
```bash
./clear_llm_cache.sh
```

### Option 2: Python Script (works without docker CLI)
```bash
python clear_cache_python.py
```

### What These Scripts Do:
- Connect to Redis
- Scan for all keys matching `llm_cache:*`
- Delete ALL cached LLM responses
- Forces fresh generation with new format

### After Clearing Cache:
1. All subsequent requests will be cache misses
2. Fresh LLM analysis with current format
3. New cache entries with compatible format
4. Frontend parsing works correctly

## Cache Hash Behavior Analysis

### Question: "How does bulk analysis hash work?"

**Answer**: Bulk analysis creates a **single cache entry for all resources** of that type.

**Example**:
- Request: Analyze ALL storage accounts (no specific resource_id)
- Hash key: `llm_cache:a1b2c3d4...` (based on: cloud, schema, type, dates, **empty resource_id**)
- Cached value: `[{resource1}, {resource2}, {resource3}, ...]` (list of ALL resources)

**Behavior**:
- Same date range + same resource type → Same hash → Returns cached list of ALL resources
- Different date range → Different hash → New analysis
- Different resource type → Different hash → New analysis

### Question: "Why does single resource work but bulk fails?"

**Answer**: Both use the same response structure. The issue is **old cached data format**.

**What's happening**:
1. Bulk request hits OLD cache → Returns data in OLD format → Frontend fails
2. Single request misses cache (new resource) → Generates NEW format → Frontend works

**Not a hashing issue, it's a format compatibility issue**.

## Prevention

### Future Changes to LLM Response Format:

When making structural changes to LLM prompts or response format:

1. **Option A**: Clear Redis cache after deployment
   ```bash
   python clear_cache_python.py
   ```

2. **Option B**: Version the cache keys
   ```python
   CACHE_VERSION = "v2"
   return f"llm_cache:{CACHE_VERSION}:{hash_key}"
   ```

3. **Option C**: Reduce cache TTL for testing
   ```python
   CACHE_TTL_SECONDS = 1 * 60 * 60  # 1 hour instead of 24
   ```

## Verification

After clearing cache, verify:

1. Backend logs show "Cache MISS" for all requests
2. Backend logs show "Cache SAVED" after LLM analysis
3. Frontend displays data correctly for both bulk and single requests
4. Second request (cache hit) also works correctly

## Summary

- ✅ **Cache hashing works correctly** (single vs bulk use different keys)
- ✅ **Response structure is consistent** (always list of dicts)
- ✅ **Backend processing works** (200 OK, valid JSON)
- ❌ **Old cached data has incompatible format** (before recent LLM prompt changes)
- ✅ **Solution: Clear Redis cache** (forces fresh generation with new format)

The issue is NOT with the caching logic or hashing mechanism. It's simply that cached data from before the LLM prompt restructuring has a different JSON format than what the current frontend expects.
