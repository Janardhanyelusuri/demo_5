-- AWS Pricing Tables (Consolidated)
-- Single table to store pricing information for all AWS resource types

-- =========================================================================
-- CONSOLIDATED PRICING TABLE
-- =========================================================================

CREATE TABLE IF NOT EXISTS __schema__.aws_pricing (
    id SERIAL PRIMARY KEY,

    -- Resource type identifier
    resource_type VARCHAR(50) NOT NULL,  -- 'ec2', 's3', 'ebs'

    -- Common pricing fields
    price DECIMAL(18, 6),
    currency VARCHAR(10),
    region VARCHAR(50),
    region_name VARCHAR(100),
    unit VARCHAR(50),
    description TEXT,

    -- EC2-specific fields (nullable)
    instance_type VARCHAR(100),
    vcpu VARCHAR(50),
    memory VARCHAR(50),
    storage VARCHAR(100),
    network_performance VARCHAR(100),
    instance_family VARCHAR(100),
    physical_processor VARCHAR(255),
    clock_speed VARCHAR(50),
    price_per_hour DECIMAL(18, 6),
    operating_system VARCHAR(50),
    tenancy VARCHAR(50),

    -- S3-specific fields (nullable)
    storage_class VARCHAR(100),
    volume_type VARCHAR(100),
    usage_type VARCHAR(100),
    price_per_unit DECIMAL(18, 6),

    -- EBS-specific fields (nullable)
    storage_media VARCHAR(50),
    max_iops_volume VARCHAR(50),
    max_throughput_volume VARCHAR(50),
    price_per_gb_month DECIMAL(18, 6),

    -- Timestamp
    last_updated TIMESTAMP DEFAULT now()
);

-- Create indexes for faster lookups
CREATE INDEX IF NOT EXISTS idx_aws_pricing_resource_type
    ON __schema__.aws_pricing(resource_type);

CREATE INDEX IF NOT EXISTS idx_aws_pricing_ec2_instance
    ON __schema__.aws_pricing(resource_type, instance_type, region)
    WHERE resource_type = 'ec2';

CREATE INDEX IF NOT EXISTS idx_aws_pricing_s3_storage
    ON __schema__.aws_pricing(resource_type, storage_class, region)
    WHERE resource_type = 's3';

CREATE INDEX IF NOT EXISTS idx_aws_pricing_ebs_volume
    ON __schema__.aws_pricing(resource_type, volume_type, region)
    WHERE resource_type = 'ebs';

CREATE INDEX IF NOT EXISTS idx_aws_pricing_region
    ON __schema__.aws_pricing(region, resource_type);

-- =========================================================================
-- RESOURCE-SPECIFIC VIEWS (Optional, for backward compatibility)
-- =========================================================================

-- EC2 Pricing View
CREATE OR REPLACE VIEW __schema__.aws_pricing_ec2 AS
SELECT
    id,
    instance_type,
    vcpu,
    memory,
    storage,
    network_performance,
    instance_family,
    physical_processor,
    clock_speed,
    price_per_hour,
    currency,
    region,
    region_name,
    operating_system,
    tenancy,
    unit,
    last_updated
FROM __schema__.aws_pricing
WHERE resource_type = 'ec2';

-- S3 Pricing View
CREATE OR REPLACE VIEW __schema__.aws_pricing_s3 AS
SELECT
    id,
    storage_class,
    volume_type,
    usage_type,
    price_per_unit,
    currency,
    region,
    region_name,
    unit,
    description,
    last_updated
FROM __schema__.aws_pricing
WHERE resource_type = 's3';

-- EBS Pricing View
CREATE OR REPLACE VIEW __schema__.aws_pricing_ebs AS
SELECT
    id,
    volume_type,
    storage_media,
    max_iops_volume,
    max_throughput_volume,
    price_per_gb_month,
    currency,
    region,
    region_name,
    unit,
    last_updated
FROM __schema__.aws_pricing
WHERE resource_type = 'ebs';

-- Grant permissions
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA __schema__ TO PUBLIC;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA __schema__ TO PUBLIC;
