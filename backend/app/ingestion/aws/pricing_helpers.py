"""
AWS Pricing Query Helpers

Provides functions to query pricing data and generate alternative instance/storage recommendations
based on resource utilization patterns.
"""

import pandas as pd
import sys
import os
from typing import Dict, List, Optional

# Add path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from app.ingestion.aws.postgres_operations import connection


@connection
def get_ec2_current_pricing(conn, schema_name: str, instance_type: str, region: str = "us-east-1") -> Optional[Dict]:
    """
    Get pricing for the current EC2 instance type.

    Args:
        conn: Database connection
        schema_name: Schema name
        instance_type: EC2 instance type (e.g., 't2.micro')
        region: AWS region

    Returns:
        Dict with pricing info or None
    """
    if not instance_type:
        return None

    query = f"""
        SELECT
            instance_type,
            vcpu,
            memory,
            network_performance,
            price_per_hour,
            currency,
            physical_processor,
            instance_family
        FROM {schema_name}.aws_pricing_ec2
        WHERE LOWER(instance_type) = LOWER(%s)
          AND LOWER(region) = LOWER(%s)
        LIMIT 1
    """

    try:
        cursor = conn.cursor()
        cursor.execute(query, (instance_type, region))
        result = cursor.fetchone()

        if result:
            return {
                'instance_type': result[0],
                'vcpu': result[1],
                'memory': result[2],
                'network_performance': result[3],
                'price_per_hour': float(result[4]) if result[4] else 0.0,
                'currency': result[5],
                'physical_processor': result[6],
                'instance_family': result[7],
                'monthly_cost': float(result[4]) * 730 if result[4] else 0.0  # 730 hours/month
            }
        return None

    except Exception as e:
        print(f"Error fetching EC2 pricing: {e}")
        return None


@connection
def get_ec2_alternative_pricing(conn, schema_name: str, current_instance: str, region: str = "us-east-1", max_results: int = 10) -> List[Dict]:
    """
    Get DIVERSE alternative EC2 instance types with pricing for comparison.
    Fetches different instance families (t3, m5, c5, r5, etc.), not just upsize/downsize of same family.

    Args:
        conn: Database connection
        schema_name: Schema name
        current_instance: Current instance type
        region: AWS region
        max_results: Maximum number of alternatives (will get mix of cheaper/similar/more expensive)

    Returns:
        List of alternative instance pricing dicts from diverse families
    """
    # First get current instance price to determine cheaper/similar/more expensive alternatives
    current_price_query = f"""
        SELECT price_per_hour
        FROM {schema_name}.aws_pricing_ec2
        WHERE LOWER(region) = LOWER(%s)
          AND instance_type = %s
        LIMIT 1
    """

    try:
        cursor = conn.cursor()
        cursor.execute(current_price_query, (region, current_instance))
        current_price_result = cursor.fetchone()
        current_price = float(current_price_result[0]) if current_price_result and current_price_result[0] else 0.0

        # Get diverse alternatives: cheaper options, similar price, and more expensive
        # Use UNION to get mix from different price ranges and different families
        query = f"""
            (
                -- Cheaper alternatives (different families)
                SELECT DISTINCT instance_type, vcpu, memory, network_performance, price_per_hour, currency, instance_family
                FROM {schema_name}.aws_pricing_ec2
                WHERE LOWER(region) = LOWER(%s)
                  AND instance_type != %s
                  AND instance_type IS NOT NULL
                  AND price_per_hour < %s
                  AND price_per_hour > 0
                ORDER BY price_per_hour DESC
                LIMIT {max(2, max_results // 2)}
            )
            UNION ALL
            (
                -- More expensive alternatives (different families for upsize)
                SELECT DISTINCT instance_type, vcpu, memory, network_performance, price_per_hour, currency, instance_family
                FROM {schema_name}.aws_pricing_ec2
                WHERE LOWER(region) = LOWER(%s)
                  AND instance_type != %s
                  AND instance_type IS NOT NULL
                  AND price_per_hour > %s
                  AND price_per_hour > 0
                ORDER BY price_per_hour ASC
                LIMIT {max(2, max_results // 2)}
            )
        """

        cursor.execute(query, (region, current_instance, current_price, region, current_instance, current_price))
        results = cursor.fetchall()

        alternatives = []
        for row in results:
            alternatives.append({
                'instance_type': row[0],
                'vcpu': row[1],
                'memory': row[2],
                'network_performance': row[3],
                'price_per_hour': float(row[4]) if row[4] else 0.0,
                'currency': row[5],
                'instance_family': row[6]
            })

        print(f"  Found {len(alternatives)} diverse alternatives across different EC2 instance families")
        return alternatives[:max_results]

    except Exception as e:
        print(f"Error fetching alternative EC2 pricing: {e}")
        return []


@connection
def get_s3_storage_class_pricing(conn, schema_name: str, region: str = "us-east-1", max_results: int = 10) -> list:
    """
    Get DIVERSE S3 storage class pricing for comparison.
    Fetches different storage classes (Standard, IA, Intelligent-Tiering, Glacier, Deep Archive).

    Args:
        conn: Database connection
        schema_name: Schema name
        region: AWS region
        max_results: Maximum number of diverse alternatives

    Returns:
        List of diverse S3 storage class pricing dicts
    """
    query = f"""
        SELECT DISTINCT
            storage_class,
            description,
            price_per_unit,
            unit,
            usage_type
        FROM {schema_name}.aws_pricing_s3
        WHERE LOWER(region) = LOWER(%s)
          AND storage_class IS NOT NULL
          AND unit LIKE '%%GB%%'
          AND price_per_unit > 0
        ORDER BY price_per_unit ASC
        LIMIT %s
    """

    try:
        cursor = conn.cursor()
        cursor.execute(query, (region, max_results))
        results = cursor.fetchall()

        storage_options = []
        for row in results:
            storage_options.append({
                'storage_class': row[0],
                'description': row[1],
                'price_per_unit': float(row[2]) if row[2] else 0.0,
                'unit': row[3],
                'usage_type': row[4]
            })

        print(f"  Found {len(storage_options)} diverse S3 storage classes")
        return storage_options

    except Exception as e:
        print(f"Error fetching S3 pricing: {e}")
        return []


@connection
def get_ebs_volume_pricing(conn, schema_name: str, region: str = "us-east-1") -> List[Dict]:
    """
    Get EBS volume pricing for different volume types.

    Args:
        conn: Database connection
        schema_name: Schema name
        region: AWS region

    Returns:
        List of EBS volume pricing dicts
    """
    query = f"""
        SELECT
            volume_type,
            storage_media,
            max_iops_volume,
            max_throughput_volume,
            price_per_gb_month,
            currency,
            unit
        FROM {schema_name}.aws_pricing_ebs
        WHERE LOWER(region) = LOWER(%s)
          AND volume_type IS NOT NULL
        ORDER BY price_per_gb_month ASC
        LIMIT 10
    """

    try:
        cursor = conn.cursor()
        cursor.execute(query, (region,))
        results = cursor.fetchall()

        volume_pricing = []
        for row in results:
            volume_pricing.append({
                'volume_type': row[0],
                'storage_media': row[1],
                'max_iops': row[2],
                'max_throughput': row[3],
                'price_per_gb_month': float(row[4]) if row[4] else 0.0,
                'currency': row[5],
                'unit': row[6]
            })

        return volume_pricing

    except Exception as e:
        print(f"Error fetching EBS pricing: {e}")
        return []


def format_ec2_pricing_for_llm(alternatives: List[Dict]) -> str:
    """
    Format EC2 pricing data for LLM context.
    Condensed format to reduce token usage.

    Args:
        alternatives: List of alternative instance pricing dicts (max 4 will be used)

    Returns:
        Formatted string for LLM prompt
    """
    if not alternatives:
        return "EC2 ALTERNATIVES: None available"

    output = []
    # Only send top 4 alternatives to reduce token usage
    for i, alt in enumerate(alternatives[:4], 1):
        output.append(f"ALT{i}: {alt['instance_type']} ({alt['vcpu']}vCPU, {alt['memory']}) = {alt['price_per_hour']:.4f}/hr")

    return "\n".join(output)


def format_s3_pricing_for_llm(s3_pricing: list) -> str:
    """
    Format S3 storage class pricing data for LLM context.
    Condensed format to reduce token usage.

    Args:
        s3_pricing: List of S3 pricing dicts

    Returns:
        Formatted string for LLM prompt
    """
    if not s3_pricing:
        return "S3 STORAGE CLASSES: Not available"

    output = []
    # Only send top 5 options to reduce token usage
    for idx, option in enumerate(s3_pricing[:5], 1):
        output.append(f"CLASS{idx}: {option['storage_class']} = {option['price_per_unit']:.5f}/{option['unit']}")

    return "\n".join(output)


def format_ebs_pricing_for_llm(ebs_pricing: List[Dict]) -> str:
    """
    Format EBS volume pricing data for LLM context.

    Args:
        ebs_pricing: List of EBS pricing dicts

    Returns:
        Formatted string for LLM prompt
    """
    if not ebs_pricing:
        return "EBS VOLUME PRICING: Not available in pricing database"

    output = ["EBS VOLUME PRICING:"]

    for vol in ebs_pricing:
        output.append(f"- {vol['volume_type']} ({vol['storage_media']}): {vol['price_per_gb_month']:.4f} per GB/month")
        if vol['max_iops']:
            output.append(f"  Max IOPS: {vol['max_iops']}")

    return "\n".join(output)
