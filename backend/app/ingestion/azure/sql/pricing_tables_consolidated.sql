-- Azure Pricing Tables (Consolidated)
-- Single table to store pricing information for all Azure resource types

-- =========================================================================
-- DROP OLD TABLES (if they exist from previous schema)
-- =========================================================================

DROP TABLE IF EXISTS __schema__.azure_pricing_vm CASCADE;
DROP TABLE IF EXISTS __schema__.azure_pricing_storage CASCADE;
DROP TABLE IF EXISTS __schema__.azure_pricing_disk CASCADE;
DROP TABLE IF EXISTS __schema__.azure_pricing_ip CASCADE;

-- =========================================================================
-- CONSOLIDATED PRICING TABLE
-- =========================================================================

CREATE TABLE IF NOT EXISTS __schema__.azure_pricing (
    id SERIAL PRIMARY KEY,

    -- Resource type identifier
    resource_type VARCHAR(50) NOT NULL,  -- 'vm', 'storage', 'disk', 'publicip'

    -- Common pricing fields
    sku_name VARCHAR(255),
    product_name VARCHAR(255),
    retail_price DECIMAL(18, 6),
    unit_price DECIMAL(18, 6),
    currency_code VARCHAR(10),
    unit_of_measure VARCHAR(50),

    -- Location
    arm_region_name VARCHAR(100),

    -- Metadata
    meter_name VARCHAR(255),
    type VARCHAR(100),
    description TEXT,

    -- VM-specific fields (nullable)
    arm_sku_name VARCHAR(255),
    pricing_tier VARCHAR(50),
    is_primary_meter_region BOOLEAN,

    -- Timestamp
    effective_start_date TIMESTAMP,
    last_updated TIMESTAMP DEFAULT now()
);

-- Create indexes for faster lookups
CREATE INDEX IF NOT EXISTS idx_azure_pricing_resource_type
    ON __schema__.azure_pricing(resource_type);

CREATE INDEX IF NOT EXISTS idx_azure_pricing_sku
    ON __schema__.azure_pricing(resource_type, sku_name, arm_region_name);

CREATE INDEX IF NOT EXISTS idx_azure_pricing_region
    ON __schema__.azure_pricing(arm_region_name, resource_type);

-- =========================================================================
-- RESOURCE-SPECIFIC VIEWS (Optional, for backward compatibility)
-- =========================================================================

-- VM Pricing View
CREATE OR REPLACE VIEW __schema__.azure_pricing_vm AS
SELECT
    id,
    sku_name,
    product_name,
    arm_sku_name,
    arm_region_name,
    retail_price,
    unit_price,
    currency_code,
    unit_of_measure,
    meter_name,
    type,
    is_primary_meter_region,
    effective_start_date,
    last_updated,
    pricing_tier
FROM __schema__.azure_pricing
WHERE resource_type = 'vm';

-- Storage Pricing View
CREATE OR REPLACE VIEW __schema__.azure_pricing_storage AS
SELECT
    id,
    sku_name,
    product_name,
    arm_region_name,
    retail_price,
    unit_price,
    currency_code,
    unit_of_measure,
    meter_name,
    type,
    effective_start_date,
    last_updated
FROM __schema__.azure_pricing
WHERE resource_type = 'storage';

-- Disk Pricing View
CREATE OR REPLACE VIEW __schema__.azure_pricing_disk AS
SELECT
    id,
    sku_name,
    product_name,
    arm_region_name,
    retail_price,
    unit_price,
    currency_code,
    unit_of_measure,
    meter_name,
    effective_start_date,
    last_updated
FROM __schema__.azure_pricing
WHERE resource_type = 'disk';

-- Public IP Pricing View
CREATE OR REPLACE VIEW __schema__.azure_pricing_ip AS
SELECT
    id,
    sku_name,
    product_name,
    arm_region_name,
    retail_price,
    unit_price,
    currency_code,
    unit_of_measure,
    meter_name,
    effective_start_date,
    last_updated
FROM __schema__.azure_pricing
WHERE resource_type = 'publicip';

-- Grant permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA __schema__ TO PUBLIC;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA __schema__ TO PUBLIC;
