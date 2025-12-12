-- gold_metrics_consolidated.sql
-- Consolidated gold layer views for all AWS resource metrics
-- This file creates unified dimension and fact views for EC2 and S3 metrics

-- =========================================================================
-- DROP OLD VIEWS (if they exist with different schemas)
-- =========================================================================
-- Drop old views to allow schema changes during migration
DROP VIEW IF EXISTS __schema__.gold_aws_metric_dim CASCADE;
DROP VIEW IF EXISTS __schema__.gold_ec2_fact_metrics CASCADE;
DROP VIEW IF EXISTS __schema__.gold_aws_fact_metrics CASCADE;
DROP VIEW IF EXISTS __schema__.gold_s3_fact_metrics CASCADE;

-- =========================================================================
-- GOLD LAYER: METRIC DIMENSION VIEW
-- =========================================================================
-- Contains unique metrics across all resource types

CREATE OR REPLACE VIEW __schema__.gold_aws_metric_dim AS
SELECT DISTINCT
    metric_name,
    unit,
    resource_type
FROM __schema__.silver_aws_metrics
WHERE metric_name IS NOT NULL
ORDER BY resource_type, metric_name;

-- =========================================================================
-- GOLD LAYER: CONSOLIDATED METRICS FACT VIEW
-- =========================================================================
-- Single fact view containing metrics for all resource types

CREATE OR REPLACE VIEW __schema__.gold_aws_fact_metrics AS
SELECT
    resource_id,
    resource_name,
    resource_type,
    region,
    account_id,
    observation_timestamp AS timestamp,
    observation_date AS event_date,
    observation_hour AS event_hour,
    metric_name,
    metric_value AS value,
    unit,
    dimensions_json,

    -- Resource-specific fields
    instance_type,          -- EC2
    availability_zone,      -- EC2
    storage_class,          -- S3
    storage_type,           -- S3
    arn,                    -- S3
    storage_classes_sample_json  -- S3
FROM __schema__.silver_aws_metrics
WHERE resource_id IS NOT NULL
ORDER BY observation_timestamp DESC;

-- =========================================================================
-- RESOURCE-SPECIFIC VIEWS (Optional, for backward compatibility)
-- =========================================================================

-- EC2 Metrics View
CREATE OR REPLACE VIEW __schema__.gold_ec2_fact_metrics AS
SELECT
    resource_id AS instance_id,
    resource_name AS instance_name,
    timestamp,
    event_date,
    region,
    metric_name,
    value,
    unit,
    dimensions_json,
    instance_type,
    availability_zone
FROM __schema__.gold_aws_fact_metrics
WHERE resource_type = 'ec2';

-- S3 Metrics View
CREATE OR REPLACE VIEW __schema__.gold_s3_fact_metrics AS
SELECT
    resource_id AS bucket_name,
    timestamp,
    metric_name,
    value,
    unit,
    storage_class,
    storage_type,
    dimensions_json,
    arn,
    storage_classes_sample_json
FROM __schema__.gold_aws_fact_metrics
WHERE resource_type = 's3';
