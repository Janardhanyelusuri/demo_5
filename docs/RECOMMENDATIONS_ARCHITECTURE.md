# Recommendations System Architecture Documentation

**Last Updated**: December 2024
**Version**: 2.0 (Consolidated Architecture)

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture Layers](#architecture-layers)
3. [Data Flow](#data-flow)
4. [Azure Recommendations](#azure-recommendations)
5. [AWS Recommendations](#aws-recommendations)
6. [LLM Integration](#llm-integration)
7. [Pricing Integration](#pricing-integration)
8. [API Endpoints](#api-endpoints)
9. [Database Schema](#database-schema)
10. [Code References](#code-references)

---

## Overview

The recommendations system provides AI-powered cost optimization suggestions for cloud resources across Azure and AWS. It uses a medallion architecture (Bronze → Silver → Gold) to progressively refine raw metrics into analytics-ready data, then leverages LLM analysis to generate actionable recommendations.

### Key Features

- **Multi-Cloud Support**: Azure (VM, Storage, Public IP) and AWS (EC2, S3)
- **Cost Forecasting**: Monthly and annual cost projections
- **Anomaly Detection**: Identifies unusual resource usage patterns
- **Contract Analysis**: Evaluates Reserved Instance/Savings Plan opportunities
- **Real-time Pricing**: Uses live pricing data from Azure/AWS APIs
- **Consolidated Architecture**: Single tables with `resource_type` filtering

### System Components

```
┌─────────────────┐
│  Cloud APIs     │  (Azure Monitor, CloudWatch, Pricing APIs)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Bronze Layer   │  (Raw metrics ingestion)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Silver Layer   │  (Consolidated metrics with resource_type)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Gold Layer    │  (Analytics-ready views)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  LLM Analysis   │  (AI-powered recommendations)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  API Response   │  (JSON recommendations to frontend)
└─────────────────┘
```

---

## Architecture Layers

### Bronze Layer (Raw Data)

**Purpose**: Store raw metrics exactly as received from cloud APIs

**Characteristics**:
- Separate tables per resource type (different API schemas)
- Minimal transformations
- Hash-based deduplication to prevent duplicate ingestion
- Timestamped for tracking ingestion

**Azure Bronze Tables**:
```sql
bronze_azure_vm_metrics
bronze_azure_storage_account_metrics
bronze_azure_public_ip_metrics
```

**AWS Bronze Tables**:
```sql
bronze_ec2_instance_metrics
bronze_s3_bucket_metrics
```

**Key Fields**:
- `hash_key`: MD5 hash for deduplication
- `ingested_at`: Ingestion timestamp
- Resource-specific metrics (CPU, memory, network, storage, etc.)

### Silver Layer (Consolidated)

**Purpose**: Consolidate metrics from all resource types into unified tables

**Characteristics**:
- **Single table per cloud** with `resource_type` column
- Standardized column names across resource types
- Resource-specific fields are nullable
- Enables cross-resource analytics

**Azure Silver Table**:
```sql
CREATE TABLE silver_azure_metrics (
    metric_observation_id    VARCHAR(64) PRIMARY KEY,
    resource_id              TEXT NOT NULL,
    resource_name            TEXT,
    resource_type            TEXT NOT NULL,  -- 'vm', 'storage', 'publicip'
    observation_timestamp    TIMESTAMP NOT NULL,
    metric_name              TEXT NOT NULL,
    metric_value             DOUBLE PRECISION,

    -- Common fields
    unit                     TEXT,
    namespace                TEXT,

    -- VM-specific (nullable)
    instance_type            TEXT,

    -- Storage-specific (nullable)
    sku                      TEXT,
    access_tier              TEXT,
    kind                     TEXT,

    -- Public IP-specific (nullable)
    ip_address               TEXT,
    allocation_method        TEXT,
    tier                     TEXT
);
```

**AWS Silver Table**:
```sql
CREATE TABLE silver_aws_metrics (
    metric_observation_id    VARCHAR(64) PRIMARY KEY,
    resource_id              TEXT NOT NULL,
    resource_type            TEXT NOT NULL,  -- 'ec2', 's3'
    observation_timestamp    TIMESTAMP NOT NULL,
    metric_name              TEXT NOT NULL,
    metric_value             DOUBLE PRECISION,

    -- EC2-specific (nullable)
    instance_type            TEXT,
    availability_zone        TEXT,

    -- S3-specific (nullable)
    storage_class            TEXT,
    arn                      TEXT
);
```

**Ingestion Process**:
1. Read from bronze tables
2. Add `resource_type` column
3. Insert into consolidated silver table with ON CONFLICT handling

**File References**:
- Azure: `backend/app/ingestion/azure/sql/silver_metrics_consolidated.sql`
- AWS: `backend/app/ingestion/aws/sql/silver_metrics_consolidated.sql`

### Gold Layer (Analytics Views)

**Purpose**: Create business-friendly views optimized for analytics and LLM consumption

**Characteristics**:
- PostgreSQL VIEWs (no data storage)
- Column renaming for clarity (e.g., `metric_value` → `value`)
- Backward-compatible resource-specific views
- Filtered by `resource_type` for performance

**Azure Gold Views**:

```sql
-- Consolidated fact view (all resources)
CREATE OR REPLACE VIEW gold_azure_fact_metrics AS
SELECT
    resource_id,
    resource_name,
    resource_type,
    observation_timestamp AS timestamp,
    metric_name,
    metric_value AS value,
    -- Resource-specific fields (all nullable)
    instance_type,          -- VM
    sku, access_tier,       -- Storage
    ip_address, tier        -- Public IP
FROM silver_azure_metrics
WHERE resource_id IS NOT NULL;

-- Backward-compatible VM view
CREATE OR REPLACE VIEW gold_azure_fact_vm_metrics AS
SELECT * FROM gold_azure_fact_metrics
WHERE resource_type = 'vm';

-- Similar views for storage and publicip
```

**AWS Gold Views**:

```sql
-- Consolidated fact view (all resources)
CREATE OR REPLACE VIEW gold_aws_fact_metrics AS
SELECT
    resource_id,
    resource_type,
    observation_timestamp AS timestamp,
    metric_value AS value,
    -- Resource-specific fields
    instance_type,      -- EC2
    storage_class       -- S3
FROM silver_aws_metrics
WHERE resource_id IS NOT NULL;

-- Resource-specific views filter by resource_type
CREATE OR REPLACE VIEW gold_ec2_fact_metrics AS
SELECT * FROM gold_aws_fact_metrics
WHERE resource_type = 'ec2';
```

**File References**:
- Azure: `backend/app/ingestion/azure/sql/gold_metrics_consolidated.sql`
- AWS: `backend/app/ingestion/aws/sql/gold_metrics_consolidated.sql`

---

## Data Flow

### 1. Metrics Ingestion Pipeline

```
Cloud API → Bronze (Raw) → Silver (Consolidated) → Gold (Views) → LLM Analysis
```

**Azure Pipeline** (`backend/app/ingestion/azure/main.py`):

```python
def run_azure_ingestion(schema_name, monthly_budget):
    # Step 1: Create bronze tables
    run_sql_file('sql/bronze_metrics.sql', schema_name, monthly_budget)
    run_sql_file('sql/bronze_storage_metrics.sql', schema_name, monthly_budget)
    run_sql_file('sql/bronze_public_ip_metrics.sql', schema_name, monthly_budget)

    # Step 2: Fetch raw metrics from Azure Monitor API
    metrics_dump(...)          # VM metrics → bronze_azure_vm_metrics
    storage_metrics_dump(...)  # Storage → bronze_azure_storage_account_metrics
    public_ip_metrics_dump(...)# Public IP → bronze_azure_public_ip_metrics

    # Step 3: Consolidate into silver layer
    run_sql_file('sql/silver_metrics_consolidated.sql', schema_name, monthly_budget)

    # Step 4: Create gold views
    run_sql_file('sql/gold_metrics_consolidated.sql', schema_name, monthly_budget)
```

**AWS Pipeline** (`backend/app/ingestion/aws/main.py`):

```python
def run_aws_ingestion(schema_name, monthly_budget):
    # Step 1: Create bronze tables
    execute_sql_files('sql/bronze_ec2_metrics.sql', schema_name, monthly_budget)
    execute_sql_files('sql/bronze_s3_metrics.sql', schema_name, monthly_budget)

    # Step 2: Fetch raw metrics from CloudWatch API
    metrics_dump_ec2(...)  # EC2 → bronze_ec2_instance_metrics
    metrics_dump_s3(...)   # S3 → bronze_s3_bucket_metrics

    # Step 3: Consolidate into silver layer
    execute_sql_files('sql/silver_metrics_consolidated.sql', schema_name, monthly_budget)

    # Step 4: Create gold views
    execute_sql_files('sql/gold_metrics_consolidated.sql', schema_name, monthly_budget)
```

### 2. Pricing Data Pipeline

```
Pricing API → Consolidated Table → Resource-Specific Views → LLM Analysis
```

**Azure Pricing** (`backend/app/ingestion/azure/pricing.py`):

```python
def fetch_and_store_pricing(schema_name):
    # Fetch from Azure Retail Prices API
    vm_pricing = fetch_azure_vm_pricing(region='eastus')
    storage_pricing = fetch_azure_storage_pricing(region='eastus')
    ip_pricing = fetch_azure_ip_pricing(region='eastus')

    # Store in consolidated table with resource_type
    store_azure_pricing(conn, schema_name, vm_pricing, pricing_type='vm')
    store_azure_pricing(conn, schema_name, storage_pricing, pricing_type='storage')
    store_azure_pricing(conn, schema_name, ip_pricing, pricing_type='publicip')

def store_azure_pricing(conn, schema_name, pricing_df, pricing_type):
    # Add resource_type column
    df_with_type = pricing_df.copy()
    df_with_type['resource_type'] = pricing_type

    # Delete old records for this resource type
    DELETE FROM azure_pricing WHERE resource_type = %s

    # Insert new data
    dump_to_postgresql(df_with_type, schema_name, "azure_pricing")
```

**Pricing Table Structure**:

```sql
-- Azure consolidated pricing table
CREATE TABLE azure_pricing (
    id SERIAL PRIMARY KEY,
    resource_type VARCHAR(50) NOT NULL,  -- 'vm', 'storage', 'disk', 'publicip'
    sku_name VARCHAR(255),
    retail_price DECIMAL(18, 6),
    -- VM-specific fields (nullable)
    arm_sku_name VARCHAR(255),
    -- Storage-specific fields (nullable)
    ...
);

-- Backward-compatible views
CREATE VIEW azure_pricing_vm AS
SELECT * FROM azure_pricing WHERE resource_type = 'vm';
```

### 3. LLM Analysis Pipeline

```
Gold Views → Fetch Resource Data → Generate Prompt → Call LLM → Parse Response → Return Recommendations
```

**Step-by-step Process**:

1. **Fetch Metrics** (from gold layer)
2. **Fetch Billing Data** (from gold_azure_fact_cost / gold_aws_fact_focus)
3. **Fetch Pricing Data** (from pricing views)
4. **Generate LLM Prompt** (with all context)
5. **Call LLM API** (using genai module)
6. **Parse JSON Response** (extract recommendations)
7. **Return to API** (structured JSON)

---

## Azure Recommendations

### Supported Resources

1. **Virtual Machines (VM)**
2. **Storage Accounts**
3. **Public IP Addresses**

### VM Recommendations

**Data Sources**:
- Metrics: `gold_azure_fact_metrics WHERE resource_type = 'vm'`
- Billing: `gold_azure_fact_cost`
- Pricing: `azure_pricing_vm` (view)

**Metrics Collected**:
```python
essential_metrics = [
    'Percentage CPU',
    'Available Memory Bytes',
    'Disk Read Operations/Sec',
    'Disk Write Operations/Sec',
    'Network In',
    'Network Out'
]
```

**Query Structure** (`backend/app/ingestion/azure/llm_data_fetch.py`):

```python
def fetch_vm_utilization_data(conn, schema_name, start_date, end_date, resource_id=None):
    """
    Fetch VM metrics with AVG, MAX, and MAX_DATE aggregations.
    """
    query = """
        WITH metric_base AS (
            SELECT
                resource_id,
                resource_name AS vm_name,
                instance_type,
                metric_name,
                value AS metric_value,
                timestamp
            FROM {schema_name}.gold_azure_fact_metrics
            WHERE resource_type = 'vm'
              AND timestamp BETWEEN %s AND %s
        ),

        metric_aggregates AS (
            SELECT
                resource_id,
                vm_name,
                instance_type,
                metric_name,
                AVG(metric_value) AS avg_value,
                MAX(metric_value) AS max_value,
                FIRST_VALUE(timestamp) OVER (
                    PARTITION BY resource_id, metric_name
                    ORDER BY metric_value DESC
                ) AS max_date
            FROM metric_base
            GROUP BY resource_id, vm_name, instance_type, metric_name
        ),

        cost_data AS (
            SELECT
                resource_id,
                SUM(billed_cost) AS total_cost,
                SUM(consumed_quantity) AS consumed_quantity
            FROM {schema_name}.gold_azure_fact_cost
            WHERE charge_period_start BETWEEN %s AND %s
            GROUP BY resource_id
        )

        SELECT
            m.resource_id,
            m.vm_name,
            m.instance_type,
            m.metrics_json,
            c.total_cost,
            c.consumed_quantity
        FROM metric_aggregates m
        LEFT JOIN cost_data c ON c.resource_id = m.resource_id
    """
```

**LLM Prompt Generation** (`backend/app/ingestion/azure/llm_analysis.py`):

```python
def generate_compute_prompt(vm_data, monthly_forecast, annual_forecast):
    """
    Generate LLM prompt with VM metrics, costs, and pricing context.
    """

    # Fetch alternative VM SKUs from pricing table
    alternative_pricing = get_vm_alternative_pricing(
        schema_name,
        current_sku=vm_data['instance_type'],
        region=vm_data['region'],
        max_results=5
    )

    prompt = f"""
    Analyze this Azure VM and provide cost optimization recommendations.

    RESOURCE: {vm_data['instance_type']} in {vm_data['region']}
    PERIOD: {vm_data['duration_days']} days
    BILLED_COST: ${vm_data['billed_cost']:.2f}
    MONTHLY_FORECAST: ${monthly_forecast:.2f}
    ANNUAL_FORECAST: ${annual_forecast:.2f}

    METRICS:
    - CPU: Avg={vm_data['cpu_avg']:.2f}%, Max={vm_data['cpu_max']:.2f}%
    - Memory: Avg={vm_data['memory_avg']:.2f}GB, Max={vm_data['memory_max']:.2f}GB
    - Network In: Avg={vm_data['network_in_avg']:.2f}GB
    - Network Out: Avg={vm_data['network_out_avg']:.2f}GB

    ALTERNATIVE VM SIZES (with pricing):
    {format_vm_pricing_for_llm(alternative_pricing)}

    INSTRUCTIONS:
    1. Analyze metrics and usage patterns
    2. Recommend right-sizing, Reserved Instances, or schedule optimizations
    3. Calculate potential savings with math
    4. Output JSON with structure below

    OUTPUT FORMAT:
    {{
      "recommendations": {{
        "effective_recommendation": {{
          "text": "Primary recommendation",
          "explanation": "Why + calculation",
          "saving_pct": 25
        }},
        "additional_recommendation": [
          {{"text": "Alternative action", "explanation": "...", "saving_pct": 15}}
        ],
        "base_of_recommendations": ["CPU: 45%", "Memory: 60%"]
      }},
      "cost_forecasting": {{"monthly": {monthly_forecast}, "annually": {annual_forecast}}},
      "anomalies": [{{"metric_name": "CPU", "timestamp": "2024-12-01", "value": 98, "reason_short": "Spike"}}],
      "contract_deal": {{
        "assessment": "good|bad|unknown",
        "for_sku": "{vm_data['instance_type']}",
        "reason": "Analysis of usage pattern",
        "monthly_saving_pct": 30,
        "annual_saving_pct": 35
      }}
    }}
    """

    return prompt
```

**Recommendation Types**:

1. **Right-sizing**: Downsize to smaller/cheaper VM SKU
2. **Reserved Instances**: Commit to 1-year or 3-year plans
3. **Spot Instances**: Use for non-critical workloads
4. **Scheduling**: Auto-shutdown during non-business hours
5. **Instance Family Change**: Switch to newer generation (e.g., Dv3 → Dv5)

### Storage Recommendations

**Data Sources**:
- Metrics: `gold_azure_fact_storage_metrics`
- Billing: `gold_azure_fact_cost`
- Pricing: `azure_pricing_storage` (view)

**Metrics Collected**:
```python
storage_metrics = [
    'UsedCapacity',      # Total storage used
    'Transactions',      # API calls
    'Ingress',          # Data uploaded
    'Egress',           # Data downloaded
    'BlobCapacity',     # Blob storage size
    'SuccessE2ELatency' # Response time
]
```

**LLM Analysis Focus**:
- Storage tier optimization (Hot → Cool → Archive)
- Lifecycle policies for old data
- Replication type optimization (LRS vs GRS)
- Unused storage accounts

### Public IP Recommendations

**Data Sources**:
- Metrics: `gold_azure_fact_publicip_metrics`
- Billing: `gold_azure_fact_cost`
- Pricing: `azure_pricing_ip` (view)

**Metrics Collected**:
```python
publicip_metrics = [
    'ByteCount',               # Data transferred
    'PacketCount',             # Packets
    'SynCount',               # TCP connections
    'VipAvailability',        # Uptime
    'IfUnderDDoSAttack'       # Security
]
```

**LLM Analysis Focus**:
- Unused/idle public IPs (release to save costs)
- Basic vs Standard SKU optimization
- Static vs Dynamic allocation

---

## AWS Recommendations

### Supported Resources

1. **EC2 Instances**
2. **S3 Buckets**

### EC2 Recommendations

**Data Sources**:
- Metrics: `gold_aws_fact_metrics WHERE resource_type = 'ec2'`
- Billing: `gold_aws_fact_focus` (FOCUS billing format)
- Pricing: `aws_pricing_ec2` (view)

**Metrics Collected**:
```python
ec2_metrics = [
    'CPUUtilization',      # CPU percentage
    'NetworkIn',           # Network ingress (bytes)
    'NetworkOut',          # Network egress (bytes)
    'DiskReadBytes',       # Disk read
    'DiskWriteBytes',      # Disk write
    'DiskReadOps',         # IOPS read
    'DiskWriteOps'         # IOPS write
]
```

**Query Structure** (`backend/app/ingestion/aws/llm_ec2_integration.py`):

```python
def fetch_ec2_utilization_data(conn, schema_name, start_date, end_date, instance_id=None):
    """
    Fetch EC2 metrics from gold layer with billing data.
    """
    query = """
        WITH metric_agg AS (
            SELECT
                m.resource_id AS instance_id,
                m.resource_name AS instance_name,
                m.instance_type,
                m.region,
                m.account_id,
                m.metric_name,
                m.value AS metric_value,
                m.timestamp
            FROM {schema_name}.gold_aws_fact_metrics m
            WHERE
                m.resource_type = 'ec2'
                AND m.timestamp BETWEEN %s AND %s
        ),

        usage_summary AS (
            SELECT
                instance_id,
                instance_name,
                instance_type,
                metric_name,
                AVG(metric_value) AS avg_value,
                MAX(metric_value) AS max_value
            FROM metric_agg
            GROUP BY instance_id, instance_name, instance_type, metric_name
        ),

        cost_summary AS (
            SELECT
                resource_id,
                SUM(billed_cost) AS billed_cost,
                SUM(consumed_quantity) AS consumed_quantity
            FROM {schema_name}.gold_aws_fact_focus
            WHERE service_name = 'Amazon Elastic Compute Cloud - Compute'
              AND charge_period_start BETWEEN %s AND %s
            GROUP BY resource_id
        )

        SELECT
            us.*,
            cs.billed_cost,
            cs.consumed_quantity
        FROM usage_summary us
        LEFT JOIN cost_summary cs
            ON LOWER(cs.resource_id) LIKE '%' || LOWER(us.instance_id) || '%'
    """
```

**Pricing Integration**:

```python
def get_ec2_alternative_pricing(schema_name, instance_type, region, max_results=4):
    """
    Fetch alternative EC2 instance types from pricing table.
    Returns diverse instance families for comparison.
    """
    query = """
        SELECT DISTINCT
            instance_type,
            vcpu,
            memory,
            price_per_hour,
            instance_family
        FROM {schema_name}.aws_pricing_ec2
        WHERE region = %s
          AND instance_type != %s
        ORDER BY price_per_hour ASC
        LIMIT %s
    """

    # Returns: [
    #   {'instance_type': 't3.medium', 'price_per_hour': 0.0416, ...},
    #   {'instance_type': 'm5.large', 'price_per_hour': 0.096, ...}
    # ]
```

**Recommendation Types**:

1. **Right-sizing**: t3.xlarge → t3.large (50% savings)
2. **Reserved Instances**: 1-year commitment (30% savings)
3. **Savings Plans**: Flexible commitment (25% savings)
4. **Spot Instances**: For fault-tolerant workloads (70% savings)
5. **Graviton Migration**: x86 → ARM (20% savings)

### S3 Recommendations

**Data Sources**:
- Metrics: `gold_aws_fact_metrics WHERE resource_type = 's3'`
- Billing: `gold_aws_fact_focus`
- Pricing: `aws_pricing_s3` (view)

**Metrics Collected**:
```python
s3_metrics = [
    'BucketSizeBytes',     # Storage size
    'NumberOfObjects',     # Object count
    'AllRequests',         # Total API calls
    'GetRequests',         # Read operations
    'PutRequests',         # Write operations
    '4xxErrors',           # Client errors
    '5xxErrors'            # Server errors
]
```

**Query Structure** (`backend/app/ingestion/aws/llm_s3_integration.py`):

```python
def fetch_s3_bucket_utilization_data(conn, schema_name, start_date, end_date, bucket_name=None):
    """
    Fetch S3 bucket metrics from gold layer.
    """
    query = """
        WITH metric_agg AS (
            SELECT
                m.resource_id AS bucket_name,
                m.account_id,
                m.region,
                m.metric_name,
                m.value AS metric_value,
                m.event_date
            FROM {schema_name}.gold_aws_fact_metrics m
            WHERE
                m.resource_type = 's3'
                AND m.event_date BETWEEN %s AND %s
        ),

        usage_summary AS (
            SELECT
                bucket_name,
                metric_name,
                AVG(
                    CASE
                        WHEN metric_name = 'BucketSizeBytes'
                        THEN metric_value / 1073741824.0  -- Convert to GB
                        ELSE metric_value
                    END
                ) AS avg_value,
                MAX(metric_value) AS max_value
            FROM metric_agg
            GROUP BY bucket_name, metric_name
        )

        SELECT * FROM usage_summary
    """
```

**Storage Class Pricing**:

```python
def get_s3_storage_class_pricing(schema_name, region, max_results=5):
    """
    Fetch S3 storage class pricing for lifecycle recommendations.
    """
    query = """
        SELECT
            storage_class,
            price_per_unit,
            unit
        FROM {schema_name}.aws_pricing_s3
        WHERE region = %s
          AND storage_class IS NOT NULL
        ORDER BY price_per_unit ASC
        LIMIT %s
    """

    # Returns: [
    #   {'storage_class': 'GLACIER_DEEP_ARCHIVE', 'price_per_unit': 0.00099},
    #   {'storage_class': 'GLACIER', 'price_per_unit': 0.004},
    #   {'storage_class': 'STANDARD_IA', 'price_per_unit': 0.0125},
    #   {'storage_class': 'STANDARD', 'price_per_unit': 0.023}
    # ]
```

**Recommendation Types**:

1. **Lifecycle Policies**: STANDARD → IA → Glacier (60% savings)
2. **Intelligent Tiering**: Automatic tier movement
3. **Storage Class Change**: Hot data → Cold storage
4. **Versioning Cleanup**: Remove old versions
5. **Incomplete Upload Cleanup**: Delete failed multipart uploads

---

## LLM Integration

### LLM Call Flow

```
Resource Data → Prompt Generation → LLM API Call → Response Parsing → Recommendation Object
```

### Core LLM Module

**File**: `backend/app/core/genai.py`

```python
def llm_call(prompt: str, model: str = "gpt-4", temperature: float = 0.1) -> str:
    """
    Call LLM API with prompt and return response.

    Args:
        prompt: Detailed analysis prompt with resource data
        model: LLM model to use
        temperature: Creativity (0.0-1.0, lower = more deterministic)

    Returns:
        LLM response as string (usually JSON)
    """

    # Configure API client
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # Call LLM
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "You are a cloud cost optimization expert."},
            {"role": "user", "content": prompt}
        ],
        temperature=temperature
    )

    return response.choices[0].message.content
```

### Prompt Engineering Strategy

**1. Context Provision**:
- Resource details (type, size, region)
- Usage metrics (CPU, memory, network, storage)
- Cost data (current, historical, forecasts)
- Pricing alternatives (from pricing tables)

**2. Instructions**:
- Analysis methodology (theoretical + mathematical)
- Output format (strict JSON schema)
- Recommendation types to consider
- Calculation requirements (show math)

**3. Output Schema Enforcement**:
```json
{
  "recommendations": {
    "effective_recommendation": {
      "text": "string",
      "explanation": "string",
      "saving_pct": 0
    },
    "additional_recommendation": [
      {"text": "string", "explanation": "string", "saving_pct": 0}
    ],
    "base_of_recommendations": ["metric: value", ...]
  },
  "cost_forecasting": {
    "monthly": 0.0,
    "annually": 0.0
  },
  "anomalies": [
    {
      "metric_name": "string",
      "timestamp": "ISO8601",
      "value": 0,
      "reason_short": "string"
    }
  ],
  "contract_deal": {
    "assessment": "good|bad|unknown",
    "for_sku": "string",
    "reason": "string",
    "monthly_saving_pct": 0,
    "annual_saving_pct": 0
  }
}
```

### Response Parsing

**Azure** (`backend/app/ingestion/azure/llm_json_extractor.py`):

```python
def extract_json(llm_response: str) -> Optional[Dict]:
    """
    Extract and parse JSON from LLM response.
    Handles markdown code blocks and malformed JSON.
    """

    # Remove markdown code blocks
    if '```json' in llm_response:
        llm_response = llm_response.split('```json')[1].split('```')[0].strip()
    elif '```' in llm_response:
        llm_response = llm_response.split('```')[1].split('```')[0].strip()

    # Parse JSON
    try:
        return json.loads(llm_response)
    except json.JSONDecodeError as e:
        LOG.error(f"Failed to parse LLM JSON: {e}")
        return None
```

**AWS** (similar inline parsing in llm_ec2_integration.py and llm_s3_integration.py)

### Task Cancellation Support

Both Azure and AWS LLM functions support task cancellation for long-running analyses:

```python
def run_llm_analysis_ec2(..., task_id: Optional[str] = None):
    """
    Run LLM analysis with cancellation support.
    """

    for idx, instance_data in enumerate(instances):
        # Check if task has been cancelled
        if task_id:
            is_cancelled = task_manager.is_cancelled(task_id)
            if is_cancelled:
                LOG.info(f"Task {task_id} was cancelled. Stopping analysis.")
                break

        # Process instance
        rec = get_ec2_recommendation_single(instance_data)
        recommendations.append(rec)

    return recommendations
```

---

## Pricing Integration

### Pricing Data Sources

**Azure**: Azure Retail Prices API
- Endpoint: `https://prices.azure.com/api/retail/prices`
- Format: REST API with JSON responses
- Coverage: VM, Storage, Disk, Public IP pricing

**AWS**: AWS Price List API
- Service: boto3 pricing client
- Region: us-east-1 (pricing API only available here)
- Coverage: EC2, S3, EBS pricing

### Pricing Fetch & Storage

**Azure Pricing Fetch** (`backend/app/ingestion/azure/pricing.py`):

```python
def fetch_azure_vm_pricing(region: str = "eastus", currency: str = "USD") -> pd.DataFrame:
    """
    Fetch Azure VM pricing from Retail Prices API.
    """

    filter_query = (
        f"serviceName eq 'Virtual Machines' "
        f"and armRegionName eq '{region}' "
        f"and priceType eq 'Consumption' "
        f"and currencyCode eq '{currency}'"
    )

    url = f"https://prices.azure.com/api/retail/prices?$filter={filter_query}"

    all_items = []
    while url:
        response = requests.get(url, timeout=30)
        data = response.json()
        all_items.extend(data.get('Items', []))
        url = data.get('NextPageLink')  # Pagination

    return pd.DataFrame(all_items)
```

**AWS Pricing Fetch** (`backend/app/ingestion/aws/pricing.py`):

```python
def fetch_ec2_pricing(aws_access_key, aws_secret_key, region="us-east-1") -> pd.DataFrame:
    """
    Fetch EC2 pricing from AWS Price List API.
    """

    pricing_client = boto3.client(
        'pricing',
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
        region_name='us-east-1'  # Pricing API only in us-east-1
    )

    filters = [
        {'Type': 'TERM_MATCH', 'Field': 'ServiceCode', 'Value': 'AmazonEC2'},
        {'Type': 'TERM_MATCH', 'Field': 'location', 'Value': 'US East (N. Virginia)'},
        {'Type': 'TERM_MATCH', 'Field': 'operatingSystem', 'Value': 'Linux'},
        {'Type': 'TERM_MATCH', 'Field': 'tenancy', 'Value': 'Shared'}
    ]

    all_products = []
    next_token = None

    while True:
        response = pricing_client.get_products(
            ServiceCode='AmazonEC2',
            Filters=filters,
            NextToken=next_token if next_token else None,
            MaxResults=100
        )

        all_products.extend(response['PriceList'])
        next_token = response.get('NextToken')

        if not next_token:
            break

    # Parse and structure data
    return parse_ec2_pricing(all_products)
```

### Consolidated Pricing Tables

**Schema**:

```sql
-- Azure Pricing
CREATE TABLE azure_pricing (
    id SERIAL PRIMARY KEY,
    resource_type VARCHAR(50) NOT NULL,  -- 'vm', 'storage', 'disk', 'publicip'

    -- Common fields
    sku_name VARCHAR(255),
    product_name VARCHAR(255),
    arm_region_name VARCHAR(100),
    retail_price DECIMAL(18, 6),
    unit_price DECIMAL(18, 6),
    currency_code VARCHAR(10),
    unit_of_measure VARCHAR(50),
    meter_name VARCHAR(255),

    -- VM-specific (nullable)
    arm_sku_name VARCHAR(255),
    pricing_tier VARCHAR(50),

    -- Timestamps
    effective_start_date TIMESTAMP,
    last_updated TIMESTAMP DEFAULT NOW()
);

-- AWS Pricing
CREATE TABLE aws_pricing (
    id SERIAL PRIMARY KEY,
    resource_type VARCHAR(50) NOT NULL,  -- 'ec2', 's3', 'ebs'

    -- EC2 fields (nullable)
    instance_type VARCHAR(100),
    vcpu VARCHAR(10),
    memory VARCHAR(50),
    storage VARCHAR(100),
    network_performance VARCHAR(100),
    instance_family VARCHAR(50),
    physical_processor VARCHAR(100),
    price_per_hour DECIMAL(18, 6),

    -- S3 fields (nullable)
    storage_class VARCHAR(100),
    price_per_unit DECIMAL(18, 10),

    -- EBS fields (nullable)
    volume_type VARCHAR(50),
    price_per_gb_month DECIMAL(18, 6),

    -- Common
    region VARCHAR(50),
    currency VARCHAR(10),
    unit VARCHAR(50),
    last_updated TIMESTAMP DEFAULT NOW()
);
```

**Backward-Compatible Views**:

```sql
-- Azure
CREATE VIEW azure_pricing_vm AS
SELECT * FROM azure_pricing WHERE resource_type = 'vm';

CREATE VIEW azure_pricing_storage AS
SELECT * FROM azure_pricing WHERE resource_type = 'storage';

-- AWS
CREATE VIEW aws_pricing_ec2 AS
SELECT * FROM aws_pricing WHERE resource_type = 'ec2';

CREATE VIEW aws_pricing_s3 AS
SELECT * FROM aws_pricing WHERE resource_type = 's3';
```

### Pricing Helpers

**Purpose**: Fetch pricing alternatives for LLM analysis

**Azure Pricing Helpers** (`backend/app/ingestion/azure/pricing_helpers.py`):

```python
def get_vm_current_pricing(schema_name: str, sku_name: str, region: str) -> Optional[Dict]:
    """Get current VM SKU pricing."""
    query = f"""
        SELECT sku_name, retail_price, currency_code, unit_of_measure
        FROM {schema_name}.azure_pricing_vm
        WHERE LOWER(sku_name) = LOWER(%s)
          AND LOWER(arm_region_name) = LOWER(%s)
        LIMIT 1
    """

def get_vm_alternative_pricing(schema_name: str, current_sku: str, region: str, max_results: int = 5):
    """Get diverse alternative VM SKUs for comparison."""
    query = f"""
        (
            -- Cheaper alternatives
            SELECT sku_name, retail_price, currency_code
            FROM {schema_name}.azure_pricing_vm
            WHERE arm_region_name = %s
              AND retail_price < (SELECT retail_price FROM ...)
            ORDER BY retail_price ASC
            LIMIT 3
        )
        UNION
        (
            -- More expensive alternatives
            SELECT sku_name, retail_price, currency_code
            FROM {schema_name}.azure_pricing_vm
            WHERE arm_region_name = %s
              AND retail_price > (SELECT retail_price FROM ...)
            ORDER BY retail_price ASC
            LIMIT 2
        )
    """
```

**AWS Pricing Helpers** (`backend/app/ingestion/aws/pricing_helpers.py`):

```python
def get_ec2_alternative_pricing(schema_name: str, instance_type: str, region: str, max_results: int = 4):
    """Get diverse EC2 instance types from different families."""
    query = f"""
        SELECT DISTINCT
            instance_type,
            vcpu,
            memory,
            price_per_hour,
            instance_family
        FROM {schema_name}.aws_pricing_ec2
        WHERE region = %s
          AND instance_type != %s
        ORDER BY price_per_hour ASC
        LIMIT %s
    """

def format_ec2_pricing_for_llm(pricing_list: List[Dict]) -> str:
    """Format pricing data for LLM prompt."""
    lines = []
    for p in pricing_list:
        lines.append(
            f"- {p['instance_type']}: ${p['price_per_hour']:.4f}/hr "
            f"({p['vcpu']} vCPU, {p['memory']} RAM)"
        )
    return "\n".join(lines)
```

---

## API Endpoints

### Azure Recommendations API

**Endpoint**: `POST /api/v1/llm/azure`

**Request Body**:
```json
{
  "resource_type": "vm",
  "start_date": "2024-11-01",
  "end_date": "2024-11-30",
  "resource_id": "/subscriptions/.../Microsoft.Compute/virtualMachines/myvm"
}
```

**Response**:
```json
[
  {
    "resource_id": "/subscriptions/.../virtualMachines/myvm",
    "recommendations": {
      "effective_recommendation": {
        "text": "Downsize from Standard_D4s_v3 to Standard_D2s_v3",
        "explanation": "CPU utilization averages 25% with max 45%. Current monthly cost: $140.16. Recommended: $70.08 (50% savings).",
        "saving_pct": 50
      },
      "additional_recommendation": [
        {
          "text": "Consider Reserved Instance for 1 year",
          "explanation": "Consistent 24/7 usage pattern. RI would provide 30% additional savings.",
          "saving_pct": 30
        }
      ],
      "base_of_recommendations": [
        "CPU Utilization: 25% avg, 45% max",
        "Memory: 60% avg",
        "Network: Low traffic"
      ]
    },
    "cost_forecasting": {
      "monthly": 140.16,
      "annually": 1681.92
    },
    "anomalies": [],
    "contract_deal": {
      "assessment": "good",
      "for_sku": "Standard_D4s_v3",
      "reason": "Consistent 24/7 usage makes RI beneficial",
      "monthly_saving_pct": 30,
      "annual_saving_pct": 35
    }
  }
]
```

**Implementation** (`backend/app/api/v1/endpoints/llm.py`):

```python
@router.post("/azure", response_model=List[Dict])
async def llm_azure(payload: LLMRequest, user: Dict = Depends(get_current_user)):
    """
    Generate LLM recommendations for Azure resources.
    """

    # Extract parameters
    schema = f"schema_{user['organization_id']}"
    resource_type = payload.resource_type.lower()

    # Route to appropriate analysis function
    if resource_type == 'vm':
        result = run_vm_llm_analysis(
            schema_name=schema,
            start_date=payload.start_date,
            end_date=payload.end_date,
            resource_id=payload.resource_id
        )
    elif resource_type in ['storage', 'storageaccount']:
        result = run_storage_llm_analysis(
            schema_name=schema,
            start_date=payload.start_date,
            end_date=payload.end_date,
            resource_id=payload.resource_id
        )
    elif resource_type == 'publicip':
        result = run_publicip_llm_analysis(
            schema_name=schema,
            start_date=payload.start_date,
            end_date=payload.end_date,
            resource_id=payload.resource_id
        )

    # Cache results
    await save_to_cache(...)

    return result
```

### AWS Recommendations API

**Endpoint**: `POST /api/v1/llm/aws`

**Request Body**:
```json
{
  "resource_type": "ec2",
  "start_date": "2024-11-01",
  "end_date": "2024-11-30",
  "resource_id": "i-0123456789abcdef0"
}
```

**Response**: (Same structure as Azure)

**Implementation**:

```python
@router.post("/aws", response_model=List[Dict])
async def llm_aws(payload: LLMRequest, user: Dict = Depends(get_current_user)):
    """
    Generate LLM recommendations for AWS resources.
    """

    schema = f"schema_{user['organization_id']}"
    resource_type = payload.resource_type.lower()
    task_id = str(uuid.uuid4())

    # Route to appropriate analysis function
    if resource_type == 'ec2':
        result = run_llm_analysis_ec2(
            resource_type=payload.resource_type,
            schema_name=schema,
            start_date=payload.start_date,
            end_date=payload.end_date,
            resource_id=payload.resource_id,
            task_id=task_id
        )
    elif resource_type == 's3':
        result = run_llm_analysis_s3(
            schema_name=schema,
            start_date=payload.start_date,
            end_date=payload.end_date,
            bucket_name=payload.resource_id,
            task_id=task_id
        )

    return result
```

---

## Database Schema

### Complete Schema Reference

```sql
-- =============================================================================
-- AZURE SCHEMA
-- =============================================================================

-- Bronze Layer (Raw Metrics)
CREATE TABLE bronze_azure_vm_metrics (
    resource_id TEXT,
    metric_name TEXT,
    timestamp TIMESTAMP,
    value DOUBLE PRECISION,
    unit TEXT,
    hash_key VARCHAR(64) PRIMARY KEY,
    ingested_at TIMESTAMP DEFAULT NOW()
);

-- Silver Layer (Consolidated)
CREATE TABLE silver_azure_metrics (
    metric_observation_id VARCHAR(64) PRIMARY KEY,
    resource_id TEXT NOT NULL,
    resource_name TEXT,
    resource_type TEXT NOT NULL,
    observation_timestamp TIMESTAMP NOT NULL,
    observation_date DATE,
    metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION,
    unit TEXT,
    namespace TEXT,
    -- VM fields
    instance_type TEXT,
    -- Storage fields
    sku TEXT,
    access_tier TEXT,
    kind TEXT,
    -- Public IP fields
    ip_address TEXT,
    allocation_method TEXT,
    tier TEXT,
    processed_at TIMESTAMP DEFAULT NOW()
);

-- Gold Layer (Views)
CREATE VIEW gold_azure_fact_metrics AS
SELECT
    resource_id,
    resource_name,
    resource_type,
    observation_timestamp AS timestamp,
    observation_date,
    metric_name,
    metric_value AS value,
    unit,
    instance_type,
    sku,
    access_tier,
    ip_address,
    tier
FROM silver_azure_metrics;

-- Pricing
CREATE TABLE azure_pricing (
    id SERIAL PRIMARY KEY,
    resource_type VARCHAR(50) NOT NULL,
    sku_name VARCHAR(255),
    retail_price DECIMAL(18, 6),
    currency_code VARCHAR(10),
    last_updated TIMESTAMP DEFAULT NOW()
);

CREATE VIEW azure_pricing_vm AS
SELECT * FROM azure_pricing WHERE resource_type = 'vm';

-- =============================================================================
-- AWS SCHEMA
-- =============================================================================

-- Bronze Layer
CREATE TABLE bronze_ec2_instance_metrics (
    instance_id TEXT,
    metric_name TEXT,
    timestamp TIMESTAMP,
    value DOUBLE PRECISION,
    unit TEXT,
    hash_key VARCHAR(64) PRIMARY KEY,
    ingested_at TIMESTAMP DEFAULT NOW()
);

-- Silver Layer
CREATE TABLE silver_aws_metrics (
    metric_observation_id VARCHAR(64) PRIMARY KEY,
    resource_id TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    observation_timestamp TIMESTAMP NOT NULL,
    observation_date DATE,
    metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION,
    unit TEXT,
    -- EC2 fields
    instance_type TEXT,
    availability_zone TEXT,
    -- S3 fields
    storage_class TEXT,
    arn TEXT,
    processed_at TIMESTAMP DEFAULT NOW()
);

-- Gold Layer
CREATE VIEW gold_aws_fact_metrics AS
SELECT
    resource_id,
    resource_type,
    observation_timestamp AS timestamp,
    observation_date AS event_date,
    metric_name,
    metric_value AS value,
    unit,
    instance_type,
    storage_class
FROM silver_aws_metrics;

-- Pricing
CREATE TABLE aws_pricing (
    id SERIAL PRIMARY KEY,
    resource_type VARCHAR(50) NOT NULL,
    instance_type VARCHAR(100),
    price_per_hour DECIMAL(18, 6),
    vcpu VARCHAR(10),
    memory VARCHAR(50),
    region VARCHAR(50),
    last_updated TIMESTAMP DEFAULT NOW()
);

CREATE VIEW aws_pricing_ec2 AS
SELECT * FROM aws_pricing WHERE resource_type = 'ec2';
```

### Indexes

**Performance Optimization**:

```sql
-- Azure
CREATE INDEX idx_silver_azure_resource_type ON silver_azure_metrics(resource_type, observation_timestamp);
CREATE INDEX idx_silver_azure_resource_id ON silver_azure_metrics(resource_id);
CREATE INDEX idx_azure_pricing_type ON azure_pricing(resource_type, sku_name);

-- AWS
CREATE INDEX idx_silver_aws_resource_type ON silver_aws_metrics(resource_type, observation_timestamp);
CREATE INDEX idx_silver_aws_resource_id ON silver_aws_metrics(resource_id);
CREATE INDEX idx_aws_pricing_type ON aws_pricing(resource_type, instance_type);
```

---

## Code References

### File Structure

```
backend/app/
├── api/v1/endpoints/
│   └── llm.py                          # API endpoints for recommendations
├── core/
│   ├── genai.py                        # LLM API integration
│   └── task_manager.py                 # Task cancellation support
├── ingestion/
│   ├── azure/
│   │   ├── main.py                     # Azure ingestion orchestration
│   │   ├── llm_data_fetch.py          # Fetch data for LLM analysis (VM, Storage, Public IP)
│   │   ├── llm_analysis.py            # Generate prompts and parse responses
│   │   ├── llm_json_extractor.py      # JSON extraction from LLM responses
│   │   ├── pricing.py                  # Azure pricing fetch & store
│   │   ├── pricing_helpers.py         # Pricing query helpers
│   │   └── sql/
│   │       ├── bronze_metrics.sql                    # Bronze VM table
│   │       ├── bronze_storage_metrics.sql            # Bronze Storage table
│   │       ├── bronze_public_ip_metrics.sql          # Bronze Public IP table
│   │       ├── silver_metrics_consolidated.sql       # Silver consolidated table
│   │       ├── gold_metrics_consolidated.sql         # Gold views
│   │       ├── pricing_tables_consolidated.sql       # Pricing table + views
│   │       └── gold.sql                              # Billing gold views
│   └── aws/
│       ├── main.py                     # AWS ingestion orchestration
│       ├── llm_ec2_integration.py     # EC2 LLM analysis
│       ├── llm_s3_integration.py      # S3 LLM analysis
│       ├── pricing.py                  # AWS pricing fetch & store
│       ├── pricing_helpers.py         # Pricing query helpers
│       └── sql/
│           ├── bronze_ec2_metrics.sql              # Bronze EC2 table
│           ├── bronze_s3_metrics.sql               # Bronze S3 table
│           ├── silver_metrics_consolidated.sql     # Silver consolidated table
│           ├── gold_metrics_consolidated.sql       # Gold views
│           └── pricing_tables_consolidated.sql     # Pricing table + views
```

### Key Functions Reference

**Azure**:
- `fetch_vm_utilization_data()` - llm_data_fetch.py:73
- `fetch_storage_utilization_data()` - llm_data_fetch.py:356
- `fetch_publicip_utilization_data()` - llm_data_fetch.py:811
- `generate_compute_prompt()` - llm_analysis.py:45
- `get_vm_alternative_pricing()` - pricing_helpers.py:87
- `store_azure_pricing()` - pricing.py:318

**AWS**:
- `fetch_ec2_utilization_data()` - llm_ec2_integration.py:44
- `fetch_s3_bucket_utilization_data()` - llm_s3_integration.py:36
- `generate_ec2_prompt()` - llm_ec2_integration.py:253
- `get_ec2_alternative_pricing()` - pricing_helpers.py:88
- `store_aws_pricing()` - pricing.py:357

**Common**:
- `llm_call()` - core/genai.py
- `extract_json()` - azure/llm_json_extractor.py

---

## Summary

This documentation covers the complete recommendations system architecture:

✅ **Medallion Architecture**: Bronze (raw) → Silver (consolidated) → Gold (analytics)
✅ **Multi-Cloud**: Azure (VM, Storage, Public IP) and AWS (EC2, S3)
✅ **LLM Integration**: AI-powered analysis with cost optimization recommendations
✅ **Pricing**: Real-time pricing from Azure/AWS APIs stored in consolidated tables
✅ **API Endpoints**: RESTful APIs for frontend integration
✅ **Database Schema**: PostgreSQL with optimized indexes

The system provides comprehensive cost optimization recommendations backed by real metrics, billing data, and live pricing information.
