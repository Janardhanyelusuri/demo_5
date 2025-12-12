-- gold_metrics_consolidated.sql
-- Consolidated gold layer views for all Azure resource metrics
-- This file creates unified dimension and fact views for VM, Storage, and Public IP metrics

-- =========================================================================
-- GOLD LAYER: METRIC DIMENSION VIEW
-- =========================================================================
-- Contains unique metrics across all resource types

CREATE OR REPLACE VIEW __schema__.gold_azure_metric_dim AS
SELECT DISTINCT
    metric_name,
    unit,
    namespace,
    resource_type
FROM __schema__.silver_azure_metrics
WHERE metric_name IS NOT NULL
ORDER BY resource_type, metric_name;

-- =========================================================================
-- GOLD LAYER: CONSOLIDATED METRICS FACT VIEW
-- =========================================================================
-- Single fact view containing metrics for all resource types

CREATE OR REPLACE VIEW __schema__.gold_azure_fact_metrics AS
SELECT
    resource_id,
    resource_name,
    resource_type,
    resource_group,
    subscription_id,
    observation_timestamp AS timestamp,
    observation_date,
    metric_name,
    metric_value AS value,
    unit,
    namespace,
    resourceregion,

    -- Resource-specific fields
    instance_type,          -- VM
    sku,                    -- All types
    access_tier,            -- Storage
    replication_type,       -- Storage
    kind,                   -- Storage
    storage_status,         -- Storage
    cost,                   -- Storage
    ip_address,             -- Public IP
    ip_version,             -- Public IP
    tier,                   -- Public IP
    allocation_method,      -- Public IP
    provisioning_state      -- Public IP
FROM __schema__.silver_azure_metrics
WHERE resource_id IS NOT NULL
ORDER BY observation_timestamp DESC;

-- =========================================================================
-- RESOURCE-SPECIFIC VIEWS (Optional, for backward compatibility)
-- =========================================================================

-- VM Metrics View
CREATE OR REPLACE VIEW __schema__.gold_azure_fact_vm_metrics AS
SELECT
    resource_id,
    resource_name AS vm_name,
    resource_group,
    subscription_id,
    timestamp,
    metric_name,
    value,
    unit,
    namespace,
    resourceregion,
    instance_type
FROM __schema__.gold_azure_fact_metrics
WHERE resource_type = 'vm';

-- Storage Metrics View
CREATE OR REPLACE VIEW __schema__.gold_azure_fact_storage_metrics AS
SELECT
    resource_id,
    resource_name AS storage_account_name,
    resource_group,
    subscription_id,
    timestamp,
    observation_date,
    metric_name,
    value,
    unit,
    namespace,
    resourceregion,
    sku,
    access_tier,
    replication_type,
    kind,
    storage_status,
    cost
FROM __schema__.gold_azure_fact_metrics
WHERE resource_type = 'storage';

-- Public IP Metrics View
CREATE OR REPLACE VIEW __schema__.gold_azure_fact_publicip_metrics AS
SELECT
    resource_id,
    resource_name AS public_ip_name,
    resource_group,
    subscription_id,
    timestamp,
    observation_date,
    metric_name,
    value,
    unit,
    namespace,
    resourceregion,
    sku,
    tier,
    ip_address,
    ip_version,
    allocation_method,
    provisioning_state
FROM __schema__.gold_azure_fact_metrics
WHERE resource_type = 'publicip';
