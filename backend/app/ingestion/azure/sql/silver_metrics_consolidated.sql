-- silver_metrics_consolidated.sql
-- Consolidated silver layer for all Azure resource metrics
-- Supports VM, Storage Account, and Public IP metrics in a single table

-- =========================================================================
-- STEP 1: CREATE CONSOLIDATED SILVER TABLE
-- =========================================================================

CREATE TABLE IF NOT EXISTS __schema__.silver_azure_metrics (
    -- Primary identifier (hash key from bronze)
    metric_observation_id    VARCHAR(64) NOT NULL PRIMARY KEY,

    -- Resource identification
    resource_id              TEXT NOT NULL,
    resource_name            TEXT,
    resource_type            TEXT NOT NULL,  -- 'vm', 'storage', 'publicip'
    resource_group           TEXT,
    subscription_id          TEXT,

    -- Time dimensions
    observation_timestamp    TIMESTAMP NOT NULL,
    observation_date         DATE,
    observation_hour         INTEGER,

    -- Metric details
    metric_name              TEXT NOT NULL,
    metric_value             DOUBLE PRECISION,
    unit                     TEXT,
    namespace                TEXT,
    displaydescription       TEXT,

    -- Location
    resourceregion           TEXT,

    -- Resource-specific fields (nullable, depending on resource_type)
    -- VM-specific
    instance_type            TEXT,  -- VM SKU

    -- Storage-specific
    sku                      TEXT,
    access_tier              TEXT,
    replication_type         TEXT,
    kind                     TEXT,
    storage_status           TEXT,
    cost                     DOUBLE PRECISION,

    -- Public IP-specific
    ip_address               TEXT,
    ip_version               TEXT,
    tier                     TEXT,
    allocation_method        TEXT,
    provisioning_state       TEXT,

    -- Audit/Lineage
    bronze_hash_key          VARCHAR(64),
    processed_at             TIMESTAMP DEFAULT now()
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS ix_silver_metrics_resource_type
    ON __schema__.silver_azure_metrics (resource_type, resource_id);

CREATE INDEX IF NOT EXISTS ix_silver_metrics_time
    ON __schema__.silver_azure_metrics (observation_date, resource_type);

CREATE INDEX IF NOT EXISTS ix_silver_metrics_resource_id
    ON __schema__.silver_azure_metrics (resource_id, observation_timestamp);

-- =========================================================================
-- STEP 2: LOAD FROM BRONZE - VM METRICS
-- =========================================================================

INSERT INTO __schema__.silver_azure_metrics (
    metric_observation_id,
    resource_id,
    resource_name,
    resource_type,
    resource_group,
    subscription_id,
    observation_timestamp,
    observation_date,
    observation_hour,
    metric_name,
    metric_value,
    unit,
    namespace,
    displaydescription,
    resourceregion,
    instance_type,
    bronze_hash_key
)
SELECT
    t1.hash_key AS metric_observation_id,
    t1.resource_id,
    t1.vm_name AS resource_name,
    'vm' AS resource_type,
    t1.resource_group,
    t1.subscription_id,
    t1.timestamp AS observation_timestamp,
    DATE(t1.timestamp) AS observation_date,
    EXTRACT(HOUR FROM t1.timestamp) AS observation_hour,
    t1.metric_name,
    t1.value AS metric_value,
    t1.unit,
    t1.namespace,
    t1.displaydescription,
    t1.resourceregion,
    t1.instance_type,
    t1.hash_key AS bronze_hash_key
FROM
    __schema__.bronze_azure_vm_metrics t1
WHERE
    t1.value IS NOT NULL
    AND t1.timestamp IS NOT NULL
    AND t1.resource_id IS NOT NULL
    AND t1.hash_key IS NOT NULL
ON CONFLICT (metric_observation_id) DO NOTHING;

-- =========================================================================
-- STEP 3: LOAD FROM BRONZE - STORAGE METRICS
-- =========================================================================

INSERT INTO __schema__.silver_azure_metrics (
    metric_observation_id,
    resource_id,
    resource_name,
    resource_type,
    resource_group,
    subscription_id,
    observation_timestamp,
    observation_date,
    observation_hour,
    metric_name,
    metric_value,
    unit,
    namespace,
    displaydescription,
    resourceregion,
    sku,
    access_tier,
    replication_type,
    kind,
    storage_status,
    cost,
    bronze_hash_key
)
SELECT
    t1.hash_key AS metric_observation_id,
    t1.resource_id,
    t1.storage_account_name AS resource_name,
    'storage' AS resource_type,
    t1.resource_group,
    t1.subscription_id,
    t1.timestamp AS observation_timestamp,
    DATE(t1.timestamp) AS observation_date,
    EXTRACT(HOUR FROM t1.timestamp) AS observation_hour,
    t1.metric_name,
    t1.value AS metric_value,
    t1.unit,
    t1.namespace,
    t1.displaydescription,
    t1.resourceregion,
    t1.sku,
    t1.access_tier,
    t1.replication AS replication_type,
    t1.kind,
    t1.storage_account_status AS storage_status,
    t1.cost,
    t1.hash_key AS bronze_hash_key
FROM
    __schema__.bronze_azure_storage_account_metrics t1
WHERE
    t1.value IS NOT NULL
    AND t1.timestamp IS NOT NULL
    AND t1.resource_id IS NOT NULL
    AND t1.hash_key IS NOT NULL
ON CONFLICT (metric_observation_id) DO NOTHING;

-- =========================================================================
-- STEP 4: LOAD FROM BRONZE - PUBLIC IP METRICS
-- =========================================================================

INSERT INTO __schema__.silver_azure_metrics (
    metric_observation_id,
    resource_id,
    resource_name,
    resource_type,
    resource_group,
    subscription_id,
    observation_timestamp,
    observation_date,
    observation_hour,
    metric_name,
    metric_value,
    unit,
    namespace,
    resourceregion,
    sku,
    tier,
    ip_address,
    ip_version,
    allocation_method,
    provisioning_state,
    bronze_hash_key
)
SELECT
    t1.hash_key AS metric_observation_id,
    t1.resource_id,
    t1.public_ip_name AS resource_name,
    'publicip' AS resource_type,
    t1.resource_group,
    t1.subscription_id,
    t1.timestamp AS observation_timestamp,
    DATE(t1.timestamp) AS observation_date,
    EXTRACT(HOUR FROM t1.timestamp) AS observation_hour,
    t1.metric_name,
    t1.value AS metric_value,
    t1.unit,
    t1.namespace,
    t1.resourceregion,
    t1.sku,
    t1.tier,
    t1.ip_address,
    t1.ip_version,
    t1.ip_allocation_method AS allocation_method,
    t1.provisioning_state,
    t1.hash_key AS bronze_hash_key
FROM
    __schema__.bronze_azure_public_ip_metrics t1
WHERE
    t1.value IS NOT NULL
    AND t1.timestamp IS NOT NULL
ON CONFLICT (metric_observation_id) DO NOTHING;
