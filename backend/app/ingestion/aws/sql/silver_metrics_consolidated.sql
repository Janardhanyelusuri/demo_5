-- silver_metrics_consolidated.sql
-- Consolidated silver layer for all AWS resource metrics
-- Supports EC2 and S3 metrics in a single table

-- =========================================================================
-- STEP 1: CREATE CONSOLIDATED SILVER TABLE
-- =========================================================================

CREATE TABLE IF NOT EXISTS __schema__.silver_aws_metrics (
    -- Primary identifier (hash key from bronze)
    metric_observation_id    VARCHAR(64) NOT NULL PRIMARY KEY,

    -- Resource identification
    resource_id              TEXT NOT NULL,  -- instance_id for EC2, bucket_name for S3
    resource_name            TEXT,           -- instance_name for EC2, bucket_name for S3
    resource_type            TEXT NOT NULL,  -- 'ec2', 's3'
    region                   TEXT,
    account_id               TEXT,

    -- Time dimensions
    observation_timestamp    TIMESTAMP NOT NULL,
    observation_date         DATE,
    observation_hour         TIMESTAMP,

    -- Metric details
    metric_name              TEXT NOT NULL,
    metric_value             DOUBLE PRECISION,
    unit                     TEXT,
    dimensions_json          JSONB,

    -- Resource-specific fields (nullable, depending on resource_type)
    -- EC2-specific
    instance_type            TEXT,
    availability_zone        TEXT,

    -- S3-specific
    storage_class            TEXT,
    storage_type             TEXT,
    arn                      TEXT,
    storage_classes_sample_json JSONB,

    -- Audit/Lineage
    bronze_hash_key          VARCHAR(64),
    ingested_at              TIMESTAMP DEFAULT now()
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS ix_silver_aws_metrics_resource_type
    ON __schema__.silver_aws_metrics (resource_type, resource_id);

CREATE INDEX IF NOT EXISTS ix_silver_aws_metrics_time
    ON __schema__.silver_aws_metrics (observation_date, resource_type);

CREATE INDEX IF NOT EXISTS ix_silver_aws_metrics_resource_id
    ON __schema__.silver_aws_metrics (resource_id, observation_timestamp);

CREATE INDEX IF NOT EXISTS ix_silver_aws_metrics_metric_name
    ON __schema__.silver_aws_metrics (metric_name);

-- =========================================================================
-- STEP 2: LOAD FROM BRONZE - EC2 METRICS
-- =========================================================================

INSERT INTO __schema__.silver_aws_metrics (
    metric_observation_id,
    resource_id,
    resource_name,
    resource_type,
    region,
    account_id,
    observation_timestamp,
    observation_date,
    observation_hour,
    metric_name,
    metric_value,
    unit,
    dimensions_json,
    instance_type,
    availability_zone,
    bronze_hash_key,
    ingested_at
)
SELECT
    t1.hash_key AS metric_observation_id,
    COALESCE(t1.instance_id, '') AS resource_id,
    COALESCE(t1.instance_name, '') AS resource_name,
    'ec2' AS resource_type,
    COALESCE(t1.region, '') AS region,
    COALESCE(t1.account_id, '') AS account_id,
    COALESCE(t1.timestamp, now())::timestamp AS observation_timestamp,
    (COALESCE(t1.timestamp, now())::timestamp)::date AS observation_date,
    date_trunc('hour', COALESCE(t1.timestamp, now())::timestamp) AS observation_hour,
    COALESCE(t1.metric_name, '') AS metric_name,
    COALESCE(t1.value, 0.0) AS metric_value,
    COALESCE(t1.unit, '') AS unit,
    t1.dimensions_json,
    COALESCE(t1.instance_type, '') AS instance_type,
    COALESCE(t1.availability_zone, '') AS availability_zone,
    t1.hash_key AS bronze_hash_key,
    t1.ingested_at
FROM
    __schema__.bronze_ec2_instance_metrics t1
WHERE
    t1.hash_key IS NOT NULL
ON CONFLICT (metric_observation_id) DO NOTHING;

-- =========================================================================
-- STEP 3: LOAD FROM BRONZE - S3 METRICS
-- =========================================================================

INSERT INTO __schema__.silver_aws_metrics (
    metric_observation_id,
    resource_id,
    resource_name,
    resource_type,
    region,
    account_id,
    observation_timestamp,
    observation_date,
    observation_hour,
    metric_name,
    metric_value,
    unit,
    dimensions_json,
    storage_class,
    storage_type,
    arn,
    storage_classes_sample_json,
    bronze_hash_key
)
SELECT
    -- Canonical hash for S3
    md5(
        LOWER(COALESCE(t1.bucket_name,'')) || '|' ||
        COALESCE(to_char(date_trunc('second', COALESCE(t1.timestamp, now())::timestamp),'YYYY-MM-DD HH24:MI:SS'),'') || '|' ||
        COALESCE(t1.metric_name,'') || '|' ||
        COALESCE(round(COALESCE(t1.value,0.0)::numeric,6)::text,'')
    ) AS metric_observation_id,
    COALESCE(t1.bucket_name, '') AS resource_id,
    COALESCE(t1.bucket_name, '') AS resource_name,
    's3' AS resource_type,
    COALESCE(t1.region, '') AS region,
    COALESCE(t1.account_id, '') AS account_id,
    COALESCE(t1.timestamp, now())::timestamp AS observation_timestamp,
    (COALESCE(t1.timestamp, now())::timestamp)::date AS observation_date,
    date_trunc('hour', COALESCE(t1.timestamp, now())::timestamp) AS observation_hour,
    COALESCE(t1.metric_name, '') AS metric_name,
    t1.value::double precision AS metric_value,
    COALESCE(t1.unit, '') AS unit,
    t1.dimensions_json,
    COALESCE(t1.storage_class, '') AS storage_class,
    COALESCE(t1.storage_type, '') AS storage_type,
    t1.arn,
    t1.storage_classes_sample_json,
    md5(
        LOWER(COALESCE(t1.bucket_name,'')) || '|' ||
        COALESCE(to_char(date_trunc('second', COALESCE(t1.timestamp, now())::timestamp),'YYYY-MM-DD HH24:MI:SS'),'') || '|' ||
        COALESCE(t1.metric_name,'') || '|' ||
        COALESCE(round(COALESCE(t1.value,0.0)::numeric,6)::text,'')
    ) AS bronze_hash_key
FROM
    __schema__.bronze_s3_bucket_metrics t1
WHERE NOT EXISTS (
    SELECT 1 FROM __schema__.silver_aws_metrics s
    WHERE s.metric_observation_id = md5(
        LOWER(COALESCE(t1.bucket_name,'')) || '|' ||
        COALESCE(to_char(date_trunc('second', COALESCE(t1.timestamp, now())::timestamp),'YYYY-MM-DD HH24:MI:SS'),'') || '|' ||
        COALESCE(t1.metric_name,'') || '|' ||
        COALESCE(round(COALESCE(t1.value,0.0)::numeric,6)::text,'')
    )
);
