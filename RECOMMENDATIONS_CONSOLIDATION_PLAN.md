# Recommendations System - Metrics & Pricing Consolidation Plan

**Date**: December 9, 2025
**Scope**: Metrics and Pricing tables only (NOT billing data/tables)
**Goal**: Consolidate metrics storage into single silver/gold layers per cloud + consolidate pricing tables

---

## 1. CURRENT ARCHITECTURE

### 1.1 Complete Function Flow (Azure VM Example)

**User Action**: User opens recommendations page → Selects resource from dropdown → Selects date range → Clicks "Analyze"

#### STEP 1: POPULATE RESOURCE DROPDOWN (Initial Page Load)

```
Frontend
  └─> Component mounts → Fetch available resources
      └─> API Call: GET /llm/azure/5/resources/vm
          │
Backend (llm.py: get_resource_ids())
  ├─> _resolve_schema_name(project_id='5') → schema='5'
  ├─> Normalize: cloud='azure', resource_type='vm'
  └─> fetch_resource_ids() with @connection decorator
      │
      ├─> Build SQL query based on resource_type:
      │   • For VM: Query gold_azure_resource_dim table
      │     WHERE service_category = 'Compute'
      │     AND resource_id LIKE '%/virtualmachines/%'
      │     Excludes Databricks VMs
      │
      │   • For Storage: Query dim_storage_account table
      │     Excludes subservices (blobservices, fileservices, etc.)
      │
      │   • For Public IP: Query dim_public_ip table
      │
      ├─> Execute: pd.read_sql_query(query, conn)
      └─> Returns: [
            {resource_id: "/subscriptions/.../vm1", resource_name: "web-server-1"},
            {resource_id: "/subscriptions/.../vm2", resource_name: "api-server-1"},
            ...
          ]
          │
Frontend
  └─> Populate dropdown with resource names
      User sees: ["web-server-1", "api-server-2", "db-server-1", ...]
```

**KEY TABLES QUERIED** (for dropdowns):
- **VM**: `gold_azure_resource_dim` (from billing FOCUS data)
- **Storage**: `dim_storage_account` (from gold_storage_metrics.sql)
- **Public IP**: `dim_public_ip` (from gold_public_ip_metrics.sql)

#### STEP 2: GENERATE RECOMMENDATIONS (User Clicks Analyze)

##### WITHOUT CACHE:
```
Frontend (dateUtils.ts)
  └─> calculateDateRange('last_week')  // IST-based: 2025-12-02 to 2025-12-09
      └─> API Call: POST /llm/azure/5
          Body: {
            resource_type: 'vm',
            resource_id: '/subscriptions/.../vm1',  // or null for all VMs
            start_date: '2025-12-02T00:00:00.000Z',
            end_date: '2025-12-09T00:00:00.000Z'
          }
          │
Backend (llm.py: llm_azure())
  ├─> _resolve_schema_name('5') → 'janardhan_project'
  ├─> Convert datetime to date: 2025-12-02, 2025-12-09
  ├─> generate_cache_hash_key()
  │   Input: "azure|janardhan_project|vm|2025-12-02|2025-12-09|/subscriptions/.../vm1"
  │   Output: "llm_cache:f1a85107ed..."
  │
  ├─> get_cached_result('llm_cache:f1a85107ed...')  // ❌ MISS
  │
  ├─> task_manager.create_task() → task_id='abc123...'
  │   (For cancellation support)
  │
  └─> run_llm_analysis()
      │
llm_data_fetch.py: run_llm_analysis()
  ├─> Normalize: rtype = 'vm'
  ├─> Route based on resource_id:
  │   • If resource_id provided: run_llm_vm() → Single dict
  │   • If resource_id is None: run_llm_vm_all_resources() → List of dicts
  │
  └─> run_llm_vm() (single resource case)
      │
      ├─> fetch_vm_utilization_data(conn, schema, start_date, end_date, resource_id)
      │   │
      │   ├─> Build complex SQL with CTEs:
      │   │
      │   │   CTE 1: metric_pivot
      │   │   └─> SELECT resource_id, metric_name, value, timestamp
      │   │       FROM gold_azure_fact_vm_metrics
      │   │       WHERE timestamp BETWEEN start_date AND end_date
      │   │       AND resource_id = '/subscriptions/.../vm1'
      │   │       AND metric_name IN ('Percentage CPU', 'Available Memory Bytes',
      │   │                            'Disk Read Operations/Sec', 'Network In', ...)
      │   │
      │   │   CTE 2: vm_details
      │   │   └─> SELECT DISTINCT resource_id, vm_name, instance_type
      │   │       FROM gold_azure_fact_vm_metrics
      │   │       (Gets VM SKU like 'Standard_D4s_v3')
      │   │
      │   │   CTE 3: metric_avg_max
      │   │   └─> SELECT resource_id, metric_name,
      │   │           AVG(value) as avg_value,  -- Convert bytes to GB for memory
      │   │           MAX(value) as max_value
      │   │       GROUP BY resource_id, metric_name
      │   │
      │   │   CTE 4: metric_max_timestamp
      │   │   └─> SELECT resource_id, metric_name, timestamp as max_timestamp
      │   │       ORDER BY metric_value DESC
      │   │       (Finds exact date of CPU/memory spikes)
      │   │
      │   │   CTE 5: metric_map
      │   │   └─> SELECT resource_id,
      │   │           json_object_agg(
      │   │             'Percentage CPU_Avg': 35.5,
      │   │             'Percentage CPU_Max': 87.3,
      │   │             'Percentage CPU_MaxDate': '2025-12-05 14:30',
      │   │             'Available Memory Bytes_Avg': 12.5,  // in GB
      │   │             ...
      │   │           ) as metrics_json
      │   │
      │   │   CTE 6: cost_agg
      │   │   └─> SELECT resource_id,
      │   │           MAX(contracted_unit_price) as unit_price,
      │   │           SUM(billed_cost) as total_cost,
      │   │           SUM(pricing_quantity) as hours_used
      │   │       FROM gold_azure_fact_cost
      │   │       WHERE charge_period_start BETWEEN dates
      │   │       GROUP BY resource_id
      │   │
      │   │   CTE 7: resource_dim
      │   │   └─> SELECT resource_id, resource_name, region_name, service_category
      │   │       FROM gold_azure_resource_dim
      │   │
      │   └─> Final SELECT:
      │       JOIN all CTEs → Returns single row with:
      │       {
      │         resource_id: "/subscriptions/.../vm1",
      │         vm_name: "web-server-1",
      │         instance_type: "Standard_D4s_v3",
      │         region_name: "East US",
      │         billed_cost: 145.67,
      │         pricing_quantity: 168,  // hours
      │         metrics_json: {
      │           "Percentage CPU_Avg": 35.5,
      │           "Percentage CPU_Max": 87.3,
      │           "Percentage CPU_MaxDate": "2025-12-05 14:30",
      │           "Available Memory Bytes_Avg": 12.5,
      │           "Network In_Avg": 2.3,
      │           ...
      │         }
      │       }
      │
      ├─> Expand metrics_json into columns (pandas json_normalize)
      │   Result: {resource_id, vm_name, instance_type, region_name, billed_cost,
      │            metric_Percentage CPU_Avg, metric_Percentage CPU_Max, ...}
      │
      ├─> Add metadata: start_date, end_date, duration_days, schema_name
      │
      ├─> Query pricing table (llm_analysis.py does this):
      │   SELECT retail_price, unit_price, meter_name
      │   FROM azure_pricing_vm
      │   WHERE sku_name = 'Standard_D4s_v3' AND arm_region_name = 'eastus'
      │   Returns: $0.192/hour retail, $0.154/hour contracted
      │
      └─> get_compute_recommendation_single(resource_row)
          │
llm_analysis.py: get_compute_recommendation_single()
  ├─> Build LLM prompt with:
  │   • VM specs: Standard_D4s_v3, 4 vCPUs, 16 GB RAM
  │   • Metrics: Avg CPU 35.5%, Max CPU 87.3% on 2025-12-05 14:30
  │   • Cost: $145.67 for 168 hours = $0.867/hour
  │   • Pricing: Retail $0.192/hr, Contracted $0.154/hr
  │   • Region: East US
  │
  ├─> Call OpenAI/Claude API (3-5 seconds)
  │   Model: gpt-4-turbo / claude-sonnet-3.5
  │   Prompt: "Analyze this VM and provide cost optimization recommendations..."
  │
  ├─> Parse JSON response from LLM
  │
  └─> Returns structured dict:
      {
        "resource_id": "/subscriptions/.../vm1",
        "recommendations": {
          "effective_recommendation": {
            "text": "Downsize to Standard_D2s_v3",
            "explanation": "CPU avg 35.5%, max 87.3%. Current: 4 vCPUs.
                           Recommended: 2 vCPUs saves 50% while handling spikes.",
            "saving_pct": 50.0
          },
          "additional_recommendation": [
            {
              "text": "Enable auto-shutdown during non-business hours",
              "explanation": "Save 65% on off-hours (16 hours/day)",
              "saving_pct": 40.0
            }
          ]
        },
        "cost_forecasting": {
          "monthly": 145.67,
          "annually": 1748.04
        },
        "anomalies": [
          {
            "metric_name": "Percentage CPU",
            "timestamp": "2025-12-05 14:30",
            "value": 87.3,
            "reason_short": "Spike detected during batch job"
          }
        ],
        "contract_deal": {
          "assessment": "good",
          "for_sku": "Standard_D4s_v3",
          "reason": "Contracted rate 20% below retail",
          "monthly_saving_pct": 20.0
        }
      }
      │
Backend (llm.py)
  ├─> save_to_cache(hash_key='llm_cache:f1a85107ed...', result, TTL=24h)
  │   Stores in Redis for instant future access
  │
  ├─> task_manager.complete_task(task_id)
  │
  └─> Return LLMResponse:
      {
        status: "success",
        cloud: "azure",
        schema_name: "janardhan_project",
        resource_type: "vm",
        recommendations: JSON.stringify(result),  // Stringified for API response
        timestamp: "2025-12-09T22:15:30Z",
        task_id: "abc123..."
      }
      │
Frontend
  └─> Parse recommendations JSON
      └─> Display in UI: charts, tables, recommendation cards
```

##### WITH CACHE:
```
Frontend → API Call → generate_cache_hash_key() → get_cached_result()
→ ✅ HIT (< 100ms) → Return cached JSON instantly
```

---

### 1.2 Backend Functions (Detailed Explanation)

#### API Endpoints (`llm.py`)

**1. `get_resource_ids(cloud_platform, project_id, resource_type)`**
- **Purpose**: Populate resource dropdown in frontend
- **Method**: GET `/llm/{cloud}/{project}/resources/{resource_type}`
- **Flow**:
  - Resolve schema from project_id
  - Build SQL query based on cloud + resource_type combination
  - For Azure VM: Query `gold_azure_resource_dim` (from billing data)
  - For Azure Storage: Query `dim_storage_account` (from metrics gold layer)
  - For Azure Public IP: Query `dim_public_ip` (from metrics gold layer)
  - For AWS EC2: Query `dim_ec2_instance`
  - For AWS S3: Query `dim_s3_bucket`
- **Returns**: List of {resource_id, resource_name} for dropdown options
- **Table Dependencies**:
  - `gold_azure_resource_dim` (billing dimension - created from FOCUS data)
  - `dim_storage_account` (metrics dimension - created in gold_storage_metrics.sql)
  - `dim_public_ip` (metrics dimension - created in gold_public_ip_metrics.sql)

**2. `llm_azure(project_id, payload: LLMRequest)`**
- **Purpose**: Generate recommendations for Azure resources
- **Method**: POST `/llm/azure/{project_id}`
- **Flow**:
  - Generate cache hash key from (cloud, schema, resource_type, dates, resource_id)
  - Check Redis cache via `get_cached_result()`
  - If MISS: Create task, call `run_llm_analysis()`, save to cache
  - If HIT: Return cached result instantly
- **Payload**: {resource_type, start_date, end_date, resource_id (optional)}
- **Returns**: LLMResponse with recommendations JSON

**3. `llm_aws(project_id, payload: LLMRequest)`**
- **Purpose**: Generate recommendations for AWS resources (EC2, S3)
- Similar flow to llm_azure

#### Ingestion Pipeline (`azure/main.py: azure_main()`)

**1. Billing Data Ingestion**
- `get_df_from_blob()` - Fetch FOCUS billing CSV from Azure Blob Storage
- `create_hash_key(df)` - Add MD5 hash column for deduplication
- `run_sql_file('create_table.sql')` - Create bronze_azure_focus table
- `dump_to_postgresql(df)` - Insert new billing records (incremental)
- `run_sql_file('silver.sql')` - Transform to silver_azure_focus
- `run_sql_file('gold.sql')` - Create gold views (resource_dim, fact_cost, etc.)

**2. VM Metrics Ingestion**
- `run_sql_file('bronze_metrics.sql')` - Create bronze_azure_vm_metrics table
- `metrics_dump(tenant_id, client_id, client_secret, subscription_id, schema, 'bronze_azure_vm_metrics')`
  - Calls Azure Monitor REST API for each VM
  - Fetches metrics: Percentage CPU, Available Memory Bytes, Disk Read/Write Ops, Network In/Out
  - Creates hash_key for deduplication
  - Inserts rows into bronze_azure_vm_metrics
- `run_sql_file('silver_metrics.sql')` - Transform to silver_azure_vm_metrics
  - INSERT INTO silver FROM bronze ON CONFLICT (hash_key) DO NOTHING
  - Deduplicates based on hash_key

**3. Storage Metrics Ingestion**
- `run_sql_file('bronze_storage_metrics.sql')` - Create bronze_azure_storage_account_metrics
- `storage_metrics_dump()` - Fetch from Azure Monitor
  - Metrics: UsedCapacity, Availability, Transactions, Ingress, Egress, etc.
- `run_sql_file('silver_storage_metrics.sql')` - Deduplicate to silver
- `run_sql_file('gold_storage_metrics.sql')` - Create star schema:
  - `dim_storage_account` - Resource metadata dimension
  - `dim_metric` - Metric type dimension
  - `dim_date` - Date dimension
  - `fact_storage_daily_usage` - Aggregated daily metrics fact table

**4. Public IP Metrics Ingestion**
- `run_sql_file('bronze_public_ip_metrics.sql')` - Create bronze table
- `public_ip_metrics_dump()` - Fetch from Azure Monitor
  - Metrics: BytesInDDoS, BytesDroppedDDoS, PacketsInDDoS, etc.
- `run_sql_file('silver_public_ip_metrics.sql')` - Deduplicate
- `run_sql_file('gold_public_ip_metrics.sql')` - Create dim_public_ip, fact tables

**5. Pricing Data Ingestion**
- `run_sql_file('pricing_tables.sql')` - Create 4 pricing tables:
  - azure_pricing_vm, azure_pricing_storage, azure_pricing_disk, azure_pricing_ip
- `fetch_and_store_all_azure_pricing(schema, region='eastus', currency='USD')`
  - Calls Azure Retail Pricing API
  - Filters by serviceName (Virtual Machines, Storage, Managed Disks, Virtual Network)
  - Inserts/updates pricing records with retail_price, unit_price, meter_name

**6. Cache Pre-warming**
- `prewarm_azure_recommendations(schema, budget)` - Generates recommendations for:
  - 3 resource types × 6 date ranges = 18 cache entries per project
  - Runs in background after ingestion completes

#### Recommendation Generation (`llm_data_fetch.py`)

**1. `run_llm_analysis(resource_type, schema, start_date, end_date, resource_id, task_id)`**
- **Purpose**: Main router function for LLM recommendations
- **Logic**:
  ```python
  if resource_type == 'vm':
      if resource_id:
          return run_llm_vm()  # Single VM
      else:
          return run_llm_vm_all_resources()  # All VMs
  elif resource_type == 'storage':
      if resource_id:
          return run_llm_storage()  # Single Storage Account
      else:
          return run_llm_storage_all_resources()  # All Storage Accounts
  elif resource_type == 'publicip':
      # Similar logic for Public IPs
  ```
- **Returns**: Single dict (if resource_id) or List of dicts (if resource_id is None)

**2. `fetch_vm_utilization_data(conn, schema, start_date, end_date, resource_id)`**
- **Purpose**: Query metrics, cost, and metadata for VMs
- **SQL Structure**:
  - CTE 1: metric_pivot - Fetch raw metrics from gold_azure_fact_vm_metrics
  - CTE 2: vm_details - Get vm_name, instance_type (SKU)
  - CTE 3: metric_avg_max - Calculate AVG and MAX for each metric, convert bytes to GB
  - CTE 4: metric_max_timestamp - Find exact timestamp of MAX value (spike detection)
  - CTE 5: metric_map - Aggregate into JSON: {metric_name_Avg, metric_name_Max, metric_name_MaxDate}
  - CTE 6: cost_agg - Sum billed_cost, pricing_quantity from gold_azure_fact_cost
  - CTE 7: resource_dim - Get resource_name, region from gold_azure_resource_dim
  - Final JOIN: Combine all CTEs into single row per VM
- **Returns**: Pandas DataFrame with flattened metrics columns

**3. `run_llm_vm(conn, schema, start_date, end_date, resource_id, task_id)`**
- **Purpose**: Generate recommendation for a single VM
- **Flow**:
  - Call `fetch_vm_utilization_data()` → Get metrics + cost dataframe
  - Convert to dict: resource_row
  - Add metadata: schema_name, region, start_date, end_date, duration_days
  - Check task cancellation (supports user abort)
  - Call `get_compute_recommendation_single(resource_row)` → LLM analysis
- **Returns**: Single recommendation dict

**4. `run_llm_vm_all_resources(conn, schema, start_date, end_date, task_id)`**
- **Purpose**: Generate recommendations for ALL VMs in project
- **Flow**:
  - Call `fetch_vm_utilization_data(resource_id=None)` → Get all VMs
  - Loop through each VM row:
    - Check task cancellation (every 10 iterations)
    - Call `get_compute_recommendation_single()` for each VM
    - Append to recommendations list
- **Returns**: List of recommendation dicts (one per VM)

**5. `fetch_storage_account_utilization_data()` and `fetch_public_ip_utilization_data()`**
- Similar structure to VM fetch function
- Query from star schema fact tables (fact_storage_daily_usage, fact_public_ip_metrics)
- Join with dimension tables (dim_storage_account, dim_metric, dim_date)

#### LLM Analysis (`llm_analysis.py`)

**1. `get_compute_recommendation_single(resource_dict)`**
- **Purpose**: Call LLM API to generate VM optimization recommendations
- **Flow**:
  - Extract metrics from resource_dict: CPU avg/max, memory, network, disk
  - Query pricing: `SELECT * FROM azure_pricing_vm WHERE sku_name = 'Standard_D4s_v3'`
  - Build prompt with:
    - Current VM specs (vCPUs, RAM, SKU)
    - Usage metrics (avg, max, spike dates)
    - Current cost (billed_cost, pricing_quantity)
    - Retail vs contracted pricing
  - Call OpenAI/Claude API with structured JSON schema
  - Parse response into standardized dict
- **Returns**: {resource_id, recommendations, cost_forecasting, anomalies, contract_deal}

**2. `get_storage_recommendation_single(resource_dict)` and `get_public_ip_recommendation_single()`**
- Similar structure to VM recommendation
- Different prompt focus:
  - Storage: Capacity utilization, access tier, redundancy optimization
  - Public IP: Idle IPs, DDoS protection cost, static vs dynamic allocation

#### Cache Pre-warming (`recommendation_prewarm.py`)

**1. `calculate_date_ranges()`**
- **Purpose**: Calculate all standard date ranges using IST timezone
- **Logic**:
  ```python
  utc_now = datetime.utcnow()
  ist_now = utc_now + timedelta(hours=5, minutes=30)
  today = ist_now.replace(hour=0, minute=0, second=0, microsecond=0)
  ```
- **Returns**: Dict with 6 date ranges: today, yesterday, last_week, last_month, last_6_months, last_year

**2. `prewarm_azure_recommendations_async(schema, budget)`**
- **Purpose**: Pre-generate recommendations for all resources and date ranges
- **Flow**:
  - Get date_ranges from calculate_date_ranges()
  - For each resource_type in ['vm', 'storage', 'publicip']:
    - For each (range_name, (start_date, end_date)) in date_ranges:
      - Generate cache_hash_key
      - Check if already cached (skip if exists)
      - Call run_llm_analysis(resource_type, dates, resource_id=None)
      - Generates recommendations for ALL resources of that type
      - Save to Redis: save_to_cache(hash_key, results, TTL=24h)
- **Result**: 18 cache entries (3 resource types × 6 date ranges)

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

## 8. ADDING NEW RESOURCES - DETAILED COMPARISON

This section explains exactly what changes are needed when adding a new resource type (e.g., Azure SQL Database).

### 8.1 SCENARIO: Adding Azure SQL Database Recommendations

**Goal**: Add recommendations for Azure SQL Database with metrics like:
- DTU/vCore utilization
- Storage used %
- Connection count
- Query performance (slow queries)

### 8.2 WITH CURRENT SEPARATE TABLES (More Work)

**Changes Required**:

**1. Create 3 New SQL Files**:
- `sql/bronze_sql_metrics.sql` - Raw metrics table
- `sql/silver_sql_metrics.sql` - Cleaned metrics table + INSERT logic
- `sql/gold_sql_metrics.sql` - Star schema (dim_sql_database, fact tables)
- Update `sql/pricing_tables.sql` - Add azure_pricing_sql table

**2. Create New Python Metrics File** (`azure/metrics_sql.py` - ~200 lines):
```python
def metrics_dump(tenant_id, client_id, client_secret, subscription_id, schema_name, table_name):
    # Get Azure access token
    # Query Azure Resource Graph for SQL databases
    # For each database:
    #   - Call Azure Monitor API
    #   - Fetch metrics: dtu_used, storage_percent, connection_successful
    #   - Create hash_key = MD5(database_id + timestamp + metric_name)
    #   - Insert into PostgreSQL bronze table
```

**3. Update Ingestion** (`azure/main.py` - 5 lines):
```python
run_sql_file('bronze_sql_metrics.sql')
sql_metrics_dump(...)
run_sql_file('silver_sql_metrics.sql')
run_sql_file('gold_sql_metrics.sql')
```

**4. Create Data Fetch Function** (`llm_data_fetch.py` - ~150 lines):
```python
@connection
def fetch_sql_utilization_data(conn, schema, start_date, end_date, resource_id=None):
    query = f"""
        -- 7 CTEs to join metrics, cost, pricing from separate tables
        WITH metric_pivot AS (
            SELECT * FROM {schema}.silver_azure_sql_metrics ...
        ),
        metric_agg AS (...),
        cost_agg AS (SELECT * FROM {schema}.gold_azure_fact_cost ...),
        resource_dim AS (SELECT * FROM {schema}.dim_sql_database ...)
        SELECT ... JOIN all tables ...
    """
    return pd.read_sql_query(query, conn)

def run_llm_sql(conn, schema, ...):
    df = fetch_sql_utilization_data(...)
    # Call LLM

def run_llm_sql_all_resources(...):
    # Loop through all SQL databases
```

**5. Update Router** (`llm_data_fetch.py: run_llm_analysis()` - 5 lines):
```python
elif rtype in ["sql", "sqldatabase"]:
    if resource_id:
        return run_llm_sql(...)
    else:
        return run_llm_sql_all_resources(...)
```

**6. Create LLM Function** (`llm_analysis.py` - ~100 lines):
```python
def get_sql_recommendation_single(resource_dict):
    # Extract SQL metrics
    # Query: SELECT * FROM azure_pricing_sql WHERE ...
    # Build LLM prompt for SQL optimization
    # Call OpenAI API
```

**7. Update Pricing** (`azure/pricing.py` - ~30 lines):
```python
def fetch_and_store_all_azure_pricing(...):
    # Fetch SQL pricing from Azure API
    # INSERT INTO azure_pricing_sql (sku_name, tier, price, ...)
```

**8. Update Dropdown** (`llm.py: get_resource_ids()` - 10 lines):
```python
elif res_type in ["sql", "sqldatabase"]:
    query = f"""
        SELECT resource_id, database_name
        FROM {schema}.dim_sql_database
        WHERE resource_id IS NOT NULL
        ORDER BY database_name LIMIT 100;
    """
```

**9. Update Pre-warming** (`recommendation_prewarm.py` - 1 line):
```python
resource_types = [..., ("sql", "SQL Database")]
```

**TOTAL**: ~500-700 new lines of code, 4 new SQL files, 1 new pricing table, 5 file updates

---

### 8.3 WITH PROPOSED CONSOLIDATED TABLES (Less Work)

**Changes Required**:

**1. Create 1 SQL File**:
- `sql/bronze_sql_metrics.sql` - Raw metrics table (bronze stays separate)

**2. Update Consolidated Silver** (`sql/silver_metrics_consolidated.sql` - add ~2 columns + INSERT):
```sql
-- ALTER existing table (add nullable columns for SQL-specific fields)
ALTER TABLE silver_azure_metrics
ADD COLUMN IF NOT EXISTS database_name TEXT,
ADD COLUMN IF NOT EXISTS server_name TEXT,
ADD COLUMN IF NOT EXISTS dtu_tier TEXT;

-- Add INSERT statement to map bronze SQL → silver (just column mapping)
INSERT INTO silver_azure_metrics (
    resource_type, resource_id, resource_name, resource_group,
    timestamp, metric_name, metric_value, unit,
    sku, database_name, server_name, dtu_tier, hash_key
)
SELECT
    'sql' AS resource_type,  -- Hardcode type
    resource_id,
    database_name AS resource_name,
    resource_group,
    timestamp,
    metric_name,
    value AS metric_value,
    unit,
    sku,
    database_name,
    server_name,
    tier AS dtu_tier,
    hash_key
FROM bronze_azure_sql_metrics
ON CONFLICT (hash_key) DO NOTHING;
```

**3. Gold Layer** - NO CHANGES NEEDED (automatically includes SQL via resource_type filter)

**4. Update Pricing** (`sql/pricing_tables_consolidated.sql` - NO schema change):
```sql
-- Just add rows to existing table
INSERT INTO azure_pricing (resource_type, sku_name, tier, retail_price, ...)
VALUES ('sql', 'S0', 'Standard', 15.00, ...);
```

**5. Create Python Metrics File** (`azure/metrics_sql.py` - ~200 lines - same as before)

**6. Update Ingestion** (`azure/main.py` - 2 lines):
```python
run_sql_file('bronze_sql_metrics.sql')
sql_metrics_dump(...)
# Silver and gold automatically handle SQL via consolidated tables!
```

**7. Create Data Fetch Function** (`llm_data_fetch.py` - ~80 lines - SIMPLER):
```python
@connection
def fetch_sql_utilization_data(conn, schema, start_date, end_date, resource_id=None):
    # Same CTE structure as VM/Storage, just add WHERE filter
    query = f"""
        WITH metric_pivot AS (
            SELECT * FROM {schema}.silver_azure_metrics
            WHERE resource_type = 'sql'  -- Just add this filter!
              AND timestamp BETWEEN %(start_date)s AND %(end_date)s
              {resource_filter}
        ),
        -- Reuse same CTEs as VM/Storage (metric_agg, cost_agg, etc.)
        ...
    """
```

**8. Update Router** - SAME (5 lines)

**9. Create LLM Function** (`llm_analysis.py` - ~100 lines):
```python
def get_sql_recommendation_single(resource_dict):
    # Query consolidated pricing
    query = "SELECT * FROM azure_pricing WHERE resource_type='sql' AND sku_name=..."
    # Rest same
```

**10. Update Pricing** (`azure/pricing.py` - ~10 lines):
```python
# Just insert rows with resource_type='sql'
INSERT INTO azure_pricing (resource_type, sku_name, ...)
VALUES ('sql', ...)
```

**11. Update Dropdown** (`llm.py: get_resource_ids()` - 5 lines - SIMPLER):
```python
elif res_type in ["sql", "sqldatabase"]:
    query = f"""
        SELECT DISTINCT resource_id, database_name as resource_name
        FROM {schema}.silver_azure_metrics
        WHERE resource_type = 'sql'  -- Query consolidated table!
        ORDER BY database_name LIMIT 100;
    """
```

**12. Update Pre-warming** - SAME (1 line)

**TOTAL**: ~300-400 new lines of code, 1 new SQL file, 0 new tables (add columns + rows), 5 file updates

---

### 8.4 SIDE-BY-SIDE COMPARISON

| Task | Separate Tables | Consolidated Tables |
|------|----------------|---------------------|
| **New Bronze Table** | ✅ Yes (1 table) | ✅ Yes (1 table - same) |
| **New Silver Table** | ❌ Yes (1 new table) | ✅ No (add columns to existing) |
| **New Gold Tables** | ❌ Yes (2-3 new tables) | ✅ No (views auto-include via filter) |
| **New Pricing Table** | ❌ Yes (1 new table) | ✅ No (add rows to existing) |
| **SQL Files** | 4 files | 1 file |
| **Python Metrics File** | 200 lines | 200 lines (same) |
| **Data Fetch Function** | 150 lines (new SQL) | 80 lines (reuse structure) |
| **LLM Analysis Function** | 100 lines | 100 lines (same) |
| **Pricing Updates** | 30 lines (new table) | 10 lines (add rows) |
| **Dropdown Updates** | 10 lines (new table query) | 5 lines (add WHERE filter) |
| **Router Updates** | 5 lines | 5 lines (same) |
| **Pre-warming Updates** | 1 line | 1 line (same) |
| **Total New Code** | ~500-700 lines | ~300-400 lines |
| **Database Objects** | +6 tables (bronze, silver, 3 gold, 1 pricing) | +1 table (bronze only) |
| **Schema Evolution** | Create new schemas | Add nullable columns |
| **Query Complexity** | Write new JOINs | Add WHERE resource_type='sql' |
| **Maintenance Burden** | High (many tables) | Low (reuse existing) |

---

### 8.5 EXAMPLE: Adding 3 More Resources

Let's say after SQL Database, you want to add:
- **Azure Cosmos DB**
- **Azure App Service**
- **AWS RDS**

#### With Separate Tables:
```
New Tables:
- 6 bronze tables (3 Azure + 1 AWS × 3 resources)
- 6 silver tables
- 12-18 gold tables (2-3 per resource)
- 4 pricing tables

New SQL Files:
- 18-24 files (bronze, silver, gold per resource)

Total New Code:
- ~1500-2100 lines (3 × 500-700 per resource)

Database Objects:
- +24-30 new tables
```

#### With Consolidated Tables:
```
New Tables:
- 4 bronze tables (resource-specific API schemas)
- 0 silver tables (add 6-8 nullable columns total)
- 0 gold tables (views auto-include via filter)
- 0 pricing tables (add rows)

New SQL Files:
- 4 files (bronze only)

Total New Code:
- ~900-1200 lines (3 × 300-400 per resource)

Database Objects:
- +4 new tables (bronze only)
```

**Scalability**: With consolidated approach, adding 10 resources = +10 bronze tables. With separate approach, adding 10 resources = +60-80 tables!

---

### 8.6 KEY INSIGHTS

**Why Consolidated is Better for New Resources**:

1. **Bronze stays separate** - Each resource has unique API schema (different metrics from Azure Monitor/CloudWatch)

2. **Silver consolidates** - After normalization, all metrics have common structure:
   - resource_id, timestamp, metric_name, metric_value
   - Resource-specific fields stored in nullable columns

3. **Gold auto-includes** - Views filter by resource_type:
   ```sql
   SELECT * FROM gold_azure_metrics_fact WHERE resource_type = 'sql'
   ```

4. **Pricing consolidates** - All resources have similar pricing schema:
   - SKU, region, retail_price, unit_price
   - Differentiated by resource_type column

5. **Queries reuse logic** - Same CTE structure for all resources:
   - metric_pivot → metric_agg → cost_agg → final SELECT
   - Just change WHERE resource_type = '...'

6. **Dropdowns simplified** - Query same silver table:
   ```sql
   SELECT * FROM silver_azure_metrics WHERE resource_type = 'sql'
   ```

**Answer to Your Question**: YES! With consolidated tables, adding new resources is much simpler. You mainly create the bronze layer (resource-specific) and map it to the unified silver schema. Everything else (gold, pricing, queries, dropdowns) works with minimal changes.

---

## 9. MIGRATION STRATEGY

### 9.1 Zero-Downtime Migration
1. Create new consolidated tables alongside old tables
2. Update ingestion to write to BOTH old and new tables
3. Update queries to read from new tables
4. Verify data consistency
5. Drop old tables

### 9.2 Data Migration
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
