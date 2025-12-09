# Recommendations System - Metrics & Pricing Consolidation Plan

**Date**: December 9, 2025
**Scope**: Metrics and Pricing tables only (NOT billing data/tables)
**Goal**: Consolidate metrics storage into single silver/gold layers per cloud + consolidate pricing tables

---

## 1. CURRENT ARCHITECTURE

### 1.1 High-Level Function Flow (Azure VM Example)

**User Action**: User selects "VM" resource type and "Last Week" date range → Clicks "Analyze"

#### WITHOUT CACHE:
```
Frontend (dateUtils.ts)
  └─> calculateDateRange('last_week')  // IST-based: 2025-12-02 to 2025-12-09
      └─> API Call: POST /llm/azure/5
          │
Backend (llm.py)
  └─> llm_azure()
      ├─> generate_cache_hash_key()  // Creates: llm_cache:f1a85107ed...
      ├─> get_cached_result()  // ❌ MISS
      └─> run_llm_analysis()
          │
llm_data_fetch.py
  └─> fetch_vm_utilization_data()
      ├─> Query: gold_azure_fact_vm_metrics (metrics data)
      ├─> Query: gold_azure_fact_cost (billing data)
      └─> Query: azure_pricing_vm (pricing data)
          │
llm_analysis.py
  └─> get_compute_recommendation_single()
      ├─> Call OpenAI/Claude LLM with metrics + costs + pricing
      └─> Returns: {recommendations, forecasting, anomalies, contract_deal}
          │
Backend (llm.py)
  └─> save_to_cache()  // Store in Redis with 24h TTL
      └─> Return JSON response to frontend
```

#### WITH CACHE:
```
Frontend → API Call → generate_cache_hash_key() → get_cached_result() → ✅ HIT → Return instantly
```

---

### 1.2 Backend Functions (High-Level)

#### Ingestion Pipeline (`azure/main.py: azure_main()`)
1. `get_df_from_blob()` - Fetch FOCUS billing data
2. `run_sql_file('bronze_metrics.sql')` - Create bronze VM metrics table
3. `metrics_dump()` - Fetch Azure Monitor metrics → bronze_azure_vm_metrics
4. `run_sql_file('silver_metrics.sql')` - Transform → silver_azure_vm_metrics
5. `run_sql_file('gold.sql')` - Create gold views (fact_vm_metrics, metric_dim)
6. `run_sql_file('bronze_storage_metrics.sql')` - Create bronze storage metrics table
7. `storage_metrics_dump()` - Fetch storage metrics → bronze_azure_storage_account_metrics
8. `run_sql_file('silver_storage_metrics.sql')` - Transform → silver_azure_storage_account_metrics
9. `run_sql_file('gold_storage_metrics.sql')` - Create storage fact/dim tables
10. `run_sql_file('bronze_public_ip_metrics.sql')` - Create bronze IP metrics table
11. `public_ip_metrics_dump()` - Fetch IP metrics → bronze_azure_public_ip_metrics
12. `run_sql_file('silver_public_ip_metrics.sql')` - Transform → silver_azure_public_ip_metrics
13. `run_sql_file('gold_public_ip_metrics.sql')` - Create IP fact/dim tables
14. `run_sql_file('pricing_tables.sql')` - Create pricing tables (4 separate tables)
15. `fetch_and_store_all_azure_pricing()` - Fetch Azure pricing → 4 pricing tables
16. `prewarm_azure_recommendations()` - Pre-cache recommendations for all date ranges

#### Recommendation Generation (`llm_data_fetch.py`)
- `run_llm_analysis()` - Main entry point
- `fetch_vm_utilization_data()` - Query metrics/cost/pricing for VMs
- `fetch_storage_utilization_data()` - Query metrics/cost/pricing for Storage
- `fetch_public_ip_utilization_data()` - Query metrics/cost/pricing for IPs

#### LLM Analysis (`llm_analysis.py`)
- `get_compute_recommendation_single()` - Generate VM recommendations
- `get_storage_recommendation_single()` - Generate storage recommendations
- `get_public_ip_recommendation_single()` - Generate IP recommendations

#### Cache Pre-warming (`recommendation_prewarm.py`)
- `calculate_date_ranges()` - Calculate IST-based date ranges
- `prewarm_azure_recommendations_async()` - Pre-generate all recommendations
- `prewarm_aws_recommendations_async()` - Pre-generate AWS recommendations

---

## 2. DATABASE TABLES (CURRENT STATE)

### 2.1 AZURE METRICS TABLES

#### Bronze Layer (Raw Ingestion - KEEP SEPARATE)
| Table Name | Purpose | Key Columns |
|------------|---------|-------------|
| `bronze_azure_vm_metrics` | Raw VM metrics from Azure Monitor | vm_name, resource_id, timestamp, metric_name, value, instance_type, hash_key |
| `bronze_azure_storage_account_metrics` | Raw storage metrics from Azure Monitor | storage_account_name, resource_id, timestamp, metric_name, value, sku, hash_key |
| `bronze_azure_public_ip_metrics` | Raw IP metrics from Azure Monitor | public_ip_name, resource_id, timestamp, metric_name, value, sku, hash_key |

**Purpose**: Receive raw data from Azure Monitor API, deduplicate via hash_key

#### Silver Layer (Cleaned - CURRENTLY SEPARATE)
| Table Name | Purpose | Key Columns |
|------------|---------|-------------|
| `silver_azure_vm_metrics` | Deduplicated VM metrics | Same as bronze, cleaned |
| `silver_azure_storage_account_metrics` | Deduplicated storage metrics | Same as bronze, cleaned |
| `silver_azure_public_ip_metrics` | Deduplicated IP metrics | Same as bronze, cleaned |

**Purpose**: Incremental load from bronze, deduplicate via ON CONFLICT (hash_key)

#### Gold Layer (Analytics - CURRENTLY SEPARATE)
| View/Table Name | Purpose | Key Columns |
|-----------------|---------|-------------|
| `gold_azure_metric_dim` | Metric metadata dimension | metric_name, unit, displaydescription, namespace |
| `gold_azure_fact_vm_metrics` | VM metrics fact table | resource_id, timestamp, metric_name, value, instance_type |
| `dim_storage_account` | Storage account dimension | storage_account_key, storage_account_name, resource_id, sku, replication_type |
| `dim_metric` | Storage metric dimension | metric_key, metric_name, unit, namespace |
| `fact_storage_daily_usage` | Daily aggregated storage metrics | date_key, storage_account_key, metric_key, daily_value_avg, observation_count |
| `dim_public_ip` | Public IP dimension | public_ip_key, public_ip_name, resource_id, sku, allocation_method |
| `fact_public_ip_metrics` | Public IP metrics fact table | resource_id, timestamp, metric_name, value |

**Purpose**: Dimensional model for analytics and LLM queries

### 2.2 AZURE PRICING TABLES (CURRENTLY SEPARATE)
| Table Name | Purpose | Key Columns |
|------------|---------|-------------|
| `azure_pricing_vm` | VM SKU pricing | sku_name, arm_region_name, retail_price, unit_price, meter_name |
| `azure_pricing_storage` | Storage account pricing | sku_name, arm_region_name, retail_price, meter_name |
| `azure_pricing_disk` | Managed disk pricing | sku_name, arm_region_name, retail_price, meter_name |
| `azure_pricing_ip` | Public IP pricing | sku_name, arm_region_name, retail_price, meter_name |

**Purpose**: Store Azure retail pricing for cost calculations and recommendations

### 2.3 AWS METRICS TABLES (SIMILAR STRUCTURE)

#### Bronze Layer
- `bronze_aws_ec2_metrics` - Raw EC2 metrics from CloudWatch
- `bronze_aws_s3_metrics` - Raw S3 metrics from CloudWatch

#### Silver Layer
- `silver_aws_ec2_metrics` - Cleaned EC2 metrics
- `silver_aws_s3_metrics` - Cleaned S3 metrics

#### Gold Layer
- `gold_aws_ec2_metrics` - EC2 analytics views
- `gold_aws_s3_metrics` - S3 analytics views

### 2.4 AWS PRICING TABLES (CURRENTLY SEPARATE)
| Table Name | Purpose | Key Columns |
|------------|---------|-------------|
| `aws_pricing_ec2` | EC2 instance pricing | instance_type, region, price_per_hour, vcpu, memory |
| `aws_pricing_s3` | S3 storage pricing | storage_class, region, price_per_unit, usage_type |
| `aws_pricing_ebs` | EBS volume pricing | volume_type, region, price_per_gb_month |

---

## 3. PROPOSED CHANGES

### 3.1 Metrics Consolidation

#### BEFORE (Current):
```
Azure:
  Bronze: bronze_azure_vm_metrics, bronze_azure_storage_account_metrics, bronze_azure_public_ip_metrics
  Silver: silver_azure_vm_metrics, silver_azure_storage_account_metrics, silver_azure_public_ip_metrics
  Gold:   Multiple views/tables per resource type

AWS:
  Bronze: bronze_aws_ec2_metrics, bronze_aws_s3_metrics
  Silver: silver_aws_ec2_metrics, silver_aws_s3_metrics
  Gold:   Multiple views per resource type
```

#### AFTER (Proposed):
```
Azure:
  Bronze: bronze_azure_vm_metrics, bronze_azure_storage_account_metrics, bronze_azure_public_ip_metrics (KEEP SEPARATE)
  Silver: silver_azure_metrics (SINGLE TABLE with resource_type column)
  Gold:   gold_azure_metrics_fact, gold_azure_metrics_dim (SINGLE FACT/DIM)

AWS:
  Bronze: bronze_aws_ec2_metrics, bronze_aws_s3_metrics (KEEP SEPARATE)
  Silver: silver_aws_metrics (SINGLE TABLE with resource_type column)
  Gold:   gold_aws_metrics_fact, gold_aws_metrics_dim (SINGLE FACT/DIM)
```

#### Rationale:
- **Bronze stays separate**: Different schemas from different APIs (Azure Monitor, CloudWatch)
- **Silver consolidates**: Common schema after normalization
- **Gold consolidates**: Unified dimensional model for all resources
- **Benefits**: Easier queries, less redundancy, simpler maintenance

### 3.2 Pricing Consolidation

#### BEFORE (Current):
```
Azure: azure_pricing_vm, azure_pricing_storage, azure_pricing_disk, azure_pricing_ip
AWS:   aws_pricing_ec2, aws_pricing_s3, aws_pricing_ebs
```

#### AFTER (Proposed):
```
Azure: azure_pricing (SINGLE TABLE with resource_type column)
AWS:   aws_pricing (SINGLE TABLE with resource_type column)
```

#### Rationale:
- All pricing data has similar schema (SKU, region, price)
- Single table simplifies queries and maintenance
- Resource type differentiation via column instead of table

---

## 4. NEW TABLE SCHEMAS

### 4.1 Consolidated Silver Metrics (Azure)

```sql
CREATE TABLE silver_azure_metrics (
    -- Common Fields
    resource_id          TEXT NOT NULL,
    resource_name        TEXT,
    resource_type        TEXT NOT NULL, -- 'vm', 'storage', 'publicip'
    resource_group       TEXT,
    subscription_id      TEXT,
    region               TEXT,

    -- Metrics
    timestamp            TIMESTAMP NOT NULL,
    metric_name          TEXT NOT NULL,
    metric_value         DOUBLE PRECISION,
    unit                 TEXT,
    namespace            TEXT,

    -- Resource-Specific Metadata (nullable)
    instance_type        TEXT,  -- VM SKU (e.g., Standard_D4s_v3)
    sku                  TEXT,  -- Storage SKU or IP SKU
    access_tier          TEXT,  -- Storage only
    replication_type     TEXT,  -- Storage only
    allocation_method    TEXT,  -- IP only

    -- System Fields
    hash_key             TEXT NOT NULL UNIQUE,
    ingested_at          TIMESTAMP DEFAULT now(),

    PRIMARY KEY (resource_id, timestamp, metric_name)
);

CREATE INDEX idx_silver_azure_metrics_resource_type ON silver_azure_metrics(resource_type);
CREATE INDEX idx_silver_azure_metrics_timestamp ON silver_azure_metrics(timestamp);
CREATE UNIQUE INDEX ux_silver_azure_metrics_hash ON silver_azure_metrics(hash_key);
```

### 4.2 Consolidated Gold Metrics (Azure)

```sql
-- Fact Table
CREATE VIEW gold_azure_metrics_fact AS
SELECT
    resource_id,
    resource_name,
    resource_type,
    resource_group,
    subscription_id,
    region,
    timestamp,
    metric_name,
    metric_value,
    unit,
    instance_type,
    sku
FROM silver_azure_metrics;

-- Dimension Table
CREATE VIEW gold_azure_metrics_dim AS
SELECT DISTINCT
    metric_name,
    unit,
    namespace,
    resource_type
FROM silver_azure_metrics
WHERE metric_name IS NOT NULL;
```

### 4.3 Consolidated Pricing (Azure)

```sql
CREATE TABLE azure_pricing (
    id                   SERIAL PRIMARY KEY,
    resource_type        VARCHAR(50) NOT NULL, -- 'vm', 'storage', 'disk', 'publicip'
    sku_name             VARCHAR(255),
    product_name         VARCHAR(255),
    arm_sku_name         VARCHAR(255),
    arm_region_name      VARCHAR(100),
    retail_price         DECIMAL(18, 6),
    unit_price           DECIMAL(18, 6),
    currency_code        VARCHAR(10),
    unit_of_measure      VARCHAR(50),
    meter_name           VARCHAR(255),
    type                 VARCHAR(100),
    effective_start_date TIMESTAMP,
    last_updated         TIMESTAMP
);

CREATE INDEX idx_azure_pricing_resource_type ON azure_pricing(resource_type);
CREATE INDEX idx_azure_pricing_sku ON azure_pricing(resource_type, sku_name, arm_region_name);
```

### 4.4 AWS Equivalent Tables

Similar structure for AWS:
- `silver_aws_metrics` (consolidated)
- `gold_aws_metrics_fact` / `gold_aws_metrics_dim` (consolidated)
- `aws_pricing` (consolidated)

---

## 5. AFFECTED FUNCTIONS AND FILES

### 5.1 SQL Files to Modify/Replace

#### Azure:
**REMOVE** (replace with consolidated versions):
- `sql/silver_metrics.sql`
- `sql/silver_storage_metrics.sql`
- `sql/silver_public_ip_metrics.sql`
- `sql/gold_storage_metrics.sql`
- `sql/gold_public_ip_metrics.sql`
- `sql/pricing_tables.sql`

**ADD** (new consolidated versions):
- `sql/silver_metrics_consolidated.sql`
- `sql/gold_metrics_consolidated.sql`
- `sql/pricing_tables_consolidated.sql`

**KEEP** (bronze layers stay separate):
- `sql/bronze_metrics.sql`
- `sql/bronze_storage_metrics.sql`
- `sql/bronze_public_ip_metrics.sql`

#### AWS:
**REMOVE**:
- `sql/silver_ec2_metrics.sql`
- `sql/silver_s3_metrics.sql`
- `sql/gold_ec2_metrics.sql`
- `sql/gold_s3_metrics.sql`
- `sql/pricing_tables.sql`

**ADD**:
- `sql/silver_metrics_consolidated.sql`
- `sql/gold_metrics_consolidated.sql`
- `sql/pricing_tables_consolidated.sql`

### 5.2 Python Functions to Update

#### `azure/main.py: azure_main()`
**BEFORE**:
```python
run_sql_file('sql/silver_metrics.sql')
run_sql_file('sql/silver_storage_metrics.sql')
run_sql_file('sql/silver_public_ip_metrics.sql')
run_sql_file('sql/gold_storage_metrics.sql')
run_sql_file('sql/gold_public_ip_metrics.sql')
run_sql_file('sql/pricing_tables.sql')
```

**AFTER**:
```python
run_sql_file('sql/silver_metrics_consolidated.sql')
run_sql_file('sql/gold_metrics_consolidated.sql')
run_sql_file('sql/pricing_tables_consolidated.sql')
```

#### `azure/llm_data_fetch.py`
**Functions to update**:
- `fetch_vm_utilization_data()` - Change FROM clause to `silver_azure_metrics WHERE resource_type='vm'`
- `fetch_storage_utilization_data()` - Change FROM clause to `silver_azure_metrics WHERE resource_type='storage'`
- `fetch_public_ip_utilization_data()` - Change FROM clause to `silver_azure_metrics WHERE resource_type='publicip'`

**Pricing queries**:
- Change FROM `azure_pricing_vm` → `azure_pricing WHERE resource_type='vm'`
- Change FROM `azure_pricing_storage` → `azure_pricing WHERE resource_type='storage'`
- Change FROM `azure_pricing_ip` → `azure_pricing WHERE resource_type='publicip'`

#### `azure/pricing.py`
**Function to update**:
- `fetch_and_store_all_azure_pricing()` - Insert into single `azure_pricing` table with `resource_type` column

#### AWS Equivalent Files:
- `aws/main.py`
- `aws/llm_s3_integration.py`
- `aws/llm_ec2_integration.py`
- `aws/pricing.py`

---

## 6. OPTIMIZATIONS

### 6.1 Query Performance
- **Before**: JOIN across multiple tables for multi-resource analysis
- **After**: Single table scan with WHERE filter on resource_type
- **Benefit**: Faster queries, simpler JOINs

### 6.2 Storage Efficiency
- **Before**: Duplicate column definitions across tables
- **After**: Single schema, nullable columns for resource-specific fields
- **Benefit**: Reduced storage overhead, easier schema evolution

### 6.3 Maintenance
- **Before**: Update 3 silver scripts + 3 gold scripts per cloud
- **After**: Update 1 silver script + 1 gold script per cloud
- **Benefit**: Less code duplication, fewer files to maintain

### 6.4 Data Consistency
- **Before**: Different transformation logic across resource types
- **After**: Unified transformation logic
- **Benefit**: Consistent data quality, easier to audit

---

## 7. USER FLOW EXPLANATION

### 7.1 User Flow WITH CACHE (Instant Response)

**User Action**: Select "VM" + "Last Week" → Click "Analyze"

```
1. Frontend (Browser - IST timezone)
   └─> calculateDateRange('last_week')
       └─> Today: 2025-12-09 00:00:00 IST
       └─> Last Week: 2025-12-02 00:00:00 IST
       └─> Generates: {startDate: '2025-12-02', endDate: '2025-12-09'}

2. Frontend sends API request:
   POST /llm/azure/5
   Body: {
     resource_type: 'vm',
     start_date: '2025-12-02T00:00:00.000Z',
     end_date: '2025-12-09T00:00:00.000Z',
     resource_id: null  // null = all VMs
   }

3. Backend (llm.py: llm_azure())
   └─> Converts datetime to date: 2025-12-02, 2025-12-09
   └─> generate_cache_hash_key()
       └─> Input: "azure|5|vm|2025-12-02|2025-12-09|"
       └─> Output: "llm_cache:f1a85107ed..."

4. Backend checks Redis cache:
   └─> get_cached_result('llm_cache:f1a85107ed...')
       └─> ✅ CACHE HIT!
       └─> Returns: [{resource_id: "...", recommendations: {...}, ...}, ...]

5. Backend returns response (< 100ms):
   └─> JSON with recommendations, forecasting, anomalies

6. Frontend displays results instantly
```

**Why cache works**:
- Pre-warm job ran at 20:00 IST using same IST date calculation
- Generated same hash key: `llm_cache:f1a85107ed...`
- Stored recommendations in Redis with 24h TTL
- Frontend request at 22:00 IST uses same hash → instant hit

---

### 7.2 User Flow WITHOUT CACHE (First Request / Cache Miss)

**User Action**: Select "Storage" + "Last Month" → Click "Analyze"

```
1. Frontend generates date range (IST):
   └─> Today: 2025-12-09, Last Month: 2025-11-09

2. API request sent:
   POST /llm/azure/5
   Body: {resource_type: 'storage', start_date: '2025-11-09', end_date: '2025-12-09'}

3. Backend (llm.py: llm_azure())
   └─> generate_cache_hash_key() → "llm_cache:a3b4c5d6..."
   └─> get_cached_result() → ❌ CACHE MISS

4. Backend calls run_llm_analysis():
   └─> llm_data_fetch.py: fetch_storage_utilization_data()
       │
       ├─> Query 1: Fetch metrics from silver_azure_metrics
       │   └─> SELECT * FROM silver_azure_metrics
       │       WHERE resource_type = 'storage'
       │       AND timestamp BETWEEN '2025-11-09' AND '2025-12-09'
       │   └─> Returns: UsedCapacity, Availability, Transactions metrics
       │
       ├─> Query 2: Fetch cost from gold_azure_fact_cost (billing)
       │   └─> SELECT SUM(effective_cost), sku, pricing_unit
       │       FROM gold_azure_fact_cost
       │       WHERE resource_id = '...' AND charge_period_start BETWEEN dates
       │   └─> Returns: $45.67 total cost
       │
       └─> Query 3: Fetch pricing from azure_pricing
           └─> SELECT retail_price, unit_price, meter_name
               FROM azure_pricing
               WHERE resource_type = 'storage' AND sku_name = 'Standard_LRS'
           └─> Returns: $0.0184/GB/month

5. Backend calls llm_analysis.py: get_storage_recommendation_single()
   └─> Formats prompt with metrics, costs, pricing
   └─> Calls OpenAI API (3-5 seconds)
   └─> Parses response into structured JSON

6. Backend saves to Redis cache:
   └─> save_to_cache('llm_cache:a3b4c5d6...', result, TTL=24h)

7. Backend returns response (3-5 seconds total):
   └─> {
         resource_id: "/subscriptions/.../storageaccounts/foo",
         recommendations: {
           effective_recommendation: {
             text: "Switch to Cool tier",
             explanation: "Usage: 120GB, Transactions: 1.2K/day...",
             saving_pct: 35
           }
         },
         cost_forecasting: {monthly: 45.67, annually: 548.04},
         anomalies: [...],
         contract_deal: {assessment: "good", ...}
       }

8. Frontend displays results after 3-5 seconds
```

**Why it takes longer**:
- Must query database (3 queries)
- Must call LLM API (OpenAI/Claude)
- Must parse and structure response
- Must save to cache

**Next request for same params**:
- Will hit cache → instant response (< 100ms)

---

### 7.3 Cache Pre-warming Flow

**Triggered by**: Ingestion job completion

```
1. Ingestion completes (azure/main.py: azure_main())
   └─> prewarm_azure_recommendations(schema_name='5', budget=10000)

2. recommendation_prewarm.py: prewarm_azure_recommendations_async()
   │
   ├─> calculate_date_ranges() using IST
   │   └─> Returns: {
   │         'today': (2025-12-09, 2025-12-09),
   │         'yesterday': (2025-12-08, 2025-12-08),
   │         'last_week': (2025-12-02, 2025-12-09),
   │         'last_month': (2025-11-09, 2025-12-09),
   │         'last_6_months': (2025-06-09, 2025-12-09),
   │         'last_year': (2024-12-09, 2025-12-09)
   │       }
   │
   ├─> For each resource_type in ['vm', 'storage', 'publicip']:
   │   └─> For each date_range in date_ranges:
   │       │
   │       ├─> generate_cache_hash_key()
   │       │   └─> Example: "llm_cache:f1a85107ed..." (vm + last_week)
   │       │
   │       ├─> Check if already cached:
   │       │   └─> get_cached_result() → skip if exists
   │       │
   │       └─> Generate recommendations:
   │           ├─> run_llm_analysis(resource_type='vm', dates, resource_id=None)
   │           ├─> Fetches ALL VMs in date range
   │           ├─> Calls LLM for bulk analysis
   │           └─> save_to_cache(hash_key, results, TTL=24h)
   │
   └─> Total: 3 resource types × 6 date ranges = 18 cache entries

3. Cache saved in Redis:
   └─> llm_cache:f1a85107ed... → [{vm1 recommendations}, {vm2 recommendations}, ...]
   └─> llm_cache:a3b4c5d6... → [{storage1 recommendations}, ...]
   └─> ... (18 keys total)
   └─> TTL: 24 hours

4. User requests within 24 hours → instant cache hits
```

---

## 8. MIGRATION STRATEGY

### 8.1 Zero-Downtime Migration
1. Create new consolidated tables alongside old tables
2. Update ingestion to write to BOTH old and new tables
3. Update queries to read from new tables
4. Verify data consistency
5. Drop old tables

### 8.2 Data Migration
- Copy existing data from old tables to new consolidated tables
- Add `resource_type` column based on source table
- Verify row counts match

---

## 9. RISKS AND MITIGATION

| Risk | Impact | Mitigation |
|------|--------|------------|
| Query performance regression | Slower recommendations | Add indexes on resource_type + timestamp |
| Data loss during migration | Lost historical data | Backup before migration, parallel writes |
| Breaking existing dashboards | Reporting breaks | Update all queries, test thoroughly |
| Cache invalidation | Cache misses | Clear Redis after schema change |

---

## 10. APPROVAL CHECKLIST

Please review and approve:

- [ ] Consolidation approach (bronze separate, silver+gold unified)
- [ ] New table schemas (single metrics + pricing tables)
- [ ] Affected functions and files
- [ ] User flow explanation (with/without cache)
- [ ] Migration strategy

**Once approved, I will proceed with implementation.**

---

## SUMMARY

**Current**: 3 bronze + 3 silver + 6+ gold tables for metrics, 4 pricing tables per cloud
**Proposed**: 3 bronze + 1 silver + 2 gold tables for metrics, 1 pricing table per cloud
**Benefit**: Simpler queries, easier maintenance, better performance, unified schema
