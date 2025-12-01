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
    Get alternative EC2 instance types with pricing for comparison.

    Args:
        conn: Database connection
        schema_name: Schema name
        current_instance: Current instance type
        region: AWS region
        max_results: Maximum number of alternatives

    Returns:
        List of alternative instance pricing dicts
    """
    # Extract instance family (e.g., 't2' from 't2.micro')
    instance_family = None
    if current_instance and '.' in current_instance:
        instance_family = current_instance.split('.')[0]

    query = f"""
        SELECT DISTINCT
            instance_type,
            vcpu,
            memory,
            network_performance,
            price_per_hour,
            currency,
            instance_family
        FROM {schema_name}.aws_pricing_ec2
        WHERE LOWER(region) = LOWER(%s)
          AND instance_type != %s
          AND instance_type IS NOT NULL
        ORDER BY price_per_hour ASC
        LIMIT %s
    """

    try:
        cursor = conn.cursor()
        cursor.execute(query, (region, current_instance, max_results))
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
                'instance_family': row[6],
                'monthly_cost': float(row[4]) * 730 if row[4] else 0.0
            })

        return alternatives

    except Exception as e:
        print(f"Error fetching alternative EC2 pricing: {e}")
        return []


@connection
def get_s3_storage_class_pricing(conn, schema_name: str, region: str = "us-east-1") -> Dict:
    """
    Get S3 storage class pricing for different tiers.

    Args:
        conn: Database connection
        schema_name: Schema name
        region: AWS region

    Returns:
        Dict with S3 pricing by storage class
    """
    query = f"""
        SELECT
            storage_class,
            description,
            price_per_unit,
            unit,
            usage_type
        FROM {schema_name}.aws_pricing_s3
        WHERE LOWER(region) = LOWER(%s)
          AND storage_class IS NOT NULL
          AND unit LIKE '%%GB%%'
        ORDER BY price_per_unit ASC
        LIMIT 20
    """

    try:
        cursor = conn.cursor()
        cursor.execute(query, (region,))
        results = cursor.fetchall()

        pricing_by_class = {}
        for row in results:
            storage_class = row[0] or 'Unknown'
            pricing_by_class[storage_class] = {
                'storage_class': row[0],
                'description': row[1],
                'price_per_unit': float(row[2]) if row[2] else 0.0,
                'unit': row[3],
                'usage_type': row[4]
            }

        return pricing_by_class

    except Exception as e:
        print(f"Error fetching S3 pricing: {e}")
        return {}


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


def format_ec2_pricing_for_llm(current_pricing: Optional[Dict], alternatives: List[Dict]) -> str:
    """
    Format EC2 pricing data for LLM context.
    Condensed format to reduce token usage.

    Args:
        current_pricing: Current instance pricing dict
        alternatives: List of alternative instance pricing dicts (max 3 will be used)

    Returns:
        Formatted string for LLM prompt
    """
    output = []

    if current_pricing:
        output.append(f"CURRENT: {current_pricing['instance_type']} ({current_pricing['vcpu']}vCPU, {current_pricing['memory']}) = {current_pricing['price_per_hour']:.4f}/hr ({current_pricing['monthly_cost']:.2f}/mo {current_pricing['currency']})")
    else:
        output.append("CURRENT: Not available")

    if alternatives:
        # Only send top 3 alternatives to reduce token usage
        curr_currency = current_pricing.get('currency', 'USD') if current_pricing else 'USD'
        for i, alt in enumerate(alternatives[:3], 1):
            savings = ""
            if current_pricing and current_pricing['monthly_cost'] > 0:
                savings_amt = current_pricing['monthly_cost'] - alt['monthly_cost']
                savings_pct = (savings_amt / current_pricing['monthly_cost']) * 100
                if savings_pct > 0:
                    savings = f" (Save {savings_pct:.0f}%)"
                elif savings_pct < 0:
                    savings = f" (+{abs(savings_pct):.0f}%)"
            output.append(f"ALT{i}: {alt['instance_type']} ({alt['vcpu']}vCPU, {alt['memory']}) = {alt['price_per_hour']:.4f}/hr ({alt['monthly_cost']:.2f}/mo){savings}")
    else:
        output.append("ALTERNATIVES: None available")

    return "\n".join(output)


def format_s3_pricing_for_llm(s3_pricing: Dict, current_class: str = "STANDARD") -> str:
    """
    Format S3 storage class pricing data for LLM context.
    Condensed format to reduce token usage.

    Args:
        s3_pricing: S3 pricing dict by storage class
        current_class: Current storage class

    Returns:
        Formatted string for LLM prompt
    """
    if not s3_pricing:
        return "S3 STORAGE PRICING: Not available"

    output = []

    # Show current class first if available
    if current_class in s3_pricing:
        info = s3_pricing[current_class]
        output.append(f"CURRENT ({current_class}): {info['price_per_unit']:.5f}/{info['unit']}")

    # Only send top 3 alternatives to reduce token usage
    alternatives_count = 0
    for storage_class, info in s3_pricing.items():
        if storage_class != current_class and alternatives_count < 3:
            output.append(f"ALT{alternatives_count + 1} ({storage_class}): {info['price_per_unit']:.5f}/{info['unit']}")
            alternatives_count += 1

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
