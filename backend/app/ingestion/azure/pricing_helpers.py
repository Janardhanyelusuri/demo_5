"""
Azure Pricing Query Helpers

Provides functions to query pricing data and generate alternative SKU recommendations
based on resource utilization patterns.
"""

import pandas as pd
import sys
import os
from typing import Dict, List, Optional

# Add path for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
from app.ingestion.azure.postgres_operation import connection


@connection
def get_vm_current_pricing(conn, schema_name: str, sku_name: str, region: str = "eastus") -> Optional[Dict]:
    """
    Get pricing for the current VM SKU.

    Args:
        conn: Database connection
        schema_name: Schema name
        sku_name: VM SKU (e.g., 'Standard_D4s_v3')
        region: Azure region

    Returns:
        Dict with pricing info or None
    """
    if not sku_name:
        return None

    query = f"""
        SELECT
            sku_name,
            product_name,
            retail_price,
            unit_price,
            currency_code,
            unit_of_measure,
            meter_name
        FROM {schema_name}.azure_pricing_vm
        WHERE LOWER(sku_name) = LOWER(%s)
          AND LOWER(arm_region_name) = LOWER(%s)
          AND meter_name LIKE '%%Compute%%'
        ORDER BY retail_price ASC
        LIMIT 1
    """

    try:
        cursor = conn.cursor()
        cursor.execute(query, (sku_name, region))
        result = cursor.fetchone()

        if result:
            return {
                'sku_name': result[0],
                'product_name': result[1],
                'retail_price': float(result[2]) if result[2] else 0.0,
                'unit_price': float(result[3]) if result[3] else 0.0,
                'currency_code': result[4],
                'unit_of_measure': result[5],
                'meter_name': result[6],
                'monthly_cost': float(result[2]) * 730 if result[2] else 0.0  # 730 hours/month
            }
        return None

    except Exception as e:
        print(f"Error fetching VM pricing: {e}")
        return None


@connection
def get_vm_alternative_pricing(conn, schema_name: str, current_sku: str, region: str = "eastus", max_results: int = 10) -> List[Dict]:
    """
    Get DIVERSE alternative VM SKUs with pricing for comparison.
    Fetches different series/families, not just upsize/downsize of same type.

    Args:
        conn: Database connection
        schema_name: Schema name
        current_sku: Current VM SKU
        region: Azure region
        max_results: Maximum number of alternatives (will try to get mix of cheaper/similar/more expensive)

    Returns:
        List of alternative SKU pricing dicts from diverse series (B, D, E, F, etc.)
    """
    # First get current SKU price to determine cheaper/similar/more expensive alternatives
    current_price_query = f"""
        SELECT retail_price
        FROM {schema_name}.azure_pricing_vm
        WHERE LOWER(arm_region_name) = LOWER(%s)
          AND sku_name = %s
          AND meter_name LIKE '%%Compute%%'
        LIMIT 1
    """

    try:
        cursor = conn.cursor()
        cursor.execute(current_price_query, (region, current_sku))
        current_price_result = cursor.fetchone()
        current_price = float(current_price_result[0]) if current_price_result and current_price_result[0] else 0.0

        # Get diverse alternatives: cheaper options, similar price, and more expensive
        # Use UNION to get mix from different price ranges and different series
        query = f"""
            (
                -- Cheaper alternatives (different series)
                SELECT DISTINCT sku_name, product_name, retail_price, currency_code, unit_of_measure, meter_name
                FROM {schema_name}.azure_pricing_vm
                WHERE LOWER(arm_region_name) = LOWER(%s)
                  AND meter_name LIKE '%%Compute%%'
                  AND sku_name != %s
                  AND sku_name IS NOT NULL
                  AND retail_price < %s
                  AND retail_price > 0
                ORDER BY retail_price DESC
                LIMIT {max(2, max_results // 2)}
            )
            UNION ALL
            (
                -- More expensive alternatives (different series for upsize)
                SELECT DISTINCT sku_name, product_name, retail_price, currency_code, unit_of_measure, meter_name
                FROM {schema_name}.azure_pricing_vm
                WHERE LOWER(arm_region_name) = LOWER(%s)
                  AND meter_name LIKE '%%Compute%%'
                  AND sku_name != %s
                  AND sku_name IS NOT NULL
                  AND retail_price > %s
                  AND retail_price > 0
                ORDER BY retail_price ASC
                LIMIT {max(2, max_results // 2)}
            )
        """

        cursor.execute(query, (region, current_sku, current_price, region, current_sku, current_price))
        results = cursor.fetchall()

        alternatives = []
        for row in results:
            alternatives.append({
                'sku_name': row[0],
                'product_name': row[1],
                'retail_price': float(row[2]) if row[2] else 0.0,
                'currency_code': row[3],
                'unit_of_measure': row[4],
                'meter_name': row[5]
            })

        print(f"  Found {len(alternatives)} diverse alternatives across different VM series")
        return alternatives[:max_results]

    except Exception as e:
        print(f"Error fetching alternative VM pricing: {e}")
        return []


@connection
def get_storage_pricing_context(conn, schema_name: str, region: str = "eastus", max_results: int = 10) -> list:
    """
    Get DIVERSE storage pricing options for comparison.
    Fetches different tiers (Hot/Cool/Archive), redundancy (LRS/GRS/ZRS), and account types.

    Args:
        conn: Database connection
        schema_name: Schema name
        region: Azure region
        max_results: Maximum number of diverse alternatives

    Returns:
        List of diverse storage option pricing dicts
    """
    # Get diverse storage options across tiers and redundancy
    query = f"""
        SELECT DISTINCT
            sku_name,
            product_name,
            meter_name,
            retail_price,
            unit_of_measure
        FROM {schema_name}.azure_pricing_storage
        WHERE LOWER(arm_region_name) = LOWER(%s)
          AND (
              meter_name LIKE '%%Data Stored%%'
              OR meter_name LIKE '%%Capacity%%'
          )
          AND retail_price > 0
        ORDER BY retail_price ASC
        LIMIT %s
    """

    try:
        cursor = conn.cursor()
        cursor.execute(query, (region, max_results))
        results = cursor.fetchall()

        alternatives = []
        for row in results:
            alternatives.append({
                'sku_name': row[0],
                'product_name': row[1],
                'meter_name': row[2],
                'retail_price': float(row[3]) if row[3] else 0.0,
                'unit_of_measure': row[4]
            })

        print(f"  Found {len(alternatives)} diverse storage options across different tiers/redundancy")
        return alternatives

    except Exception as e:
        print(f"Error fetching storage pricing: {e}")
        return []


@connection
def get_public_ip_pricing_context(conn, schema_name: str, region: str = "eastus", max_results: int = 5) -> list:
    """
    Get DIVERSE public IP pricing options for comparison.
    Fetches different SKUs (Basic/Standard) and allocation methods (Static/Dynamic).

    Args:
        conn: Database connection
        schema_name: Schema name
        region: Azure region
        max_results: Maximum number of diverse alternatives

    Returns:
        List of diverse public IP option pricing dicts
    """
    query = f"""
        SELECT DISTINCT
            sku_name,
            product_name,
            meter_name,
            retail_price,
            unit_of_measure
        FROM {schema_name}.azure_pricing_ip
        WHERE LOWER(arm_region_name) = LOWER(%s)
          AND retail_price > 0
        ORDER BY retail_price ASC
        LIMIT %s
    """

    try:
        cursor = conn.cursor()
        cursor.execute(query, (region, max_results))
        results = cursor.fetchall()

        pricing_options = []
        for row in results:
            pricing_options.append({
                'sku_name': row[0],
                'product_name': row[1],
                'meter_name': row[2],
                'retail_price': float(row[3]) if row[3] else 0.0,
                'unit_of_measure': row[4]
            })

        print(f"  Found {len(pricing_options)} diverse public IP options")
        return pricing_options

    except Exception as e:
        print(f"Error fetching IP pricing: {e}")
        return []


def format_vm_pricing_for_llm(current_pricing: Optional[Dict], alternatives: List[Dict]) -> str:
    """
    Format VM pricing data for LLM context.
    Condensed format to reduce token usage.

    Args:
        current_pricing: Current SKU pricing dict
        alternatives: List of alternative SKU pricing dicts (max 3 will be used)

    Returns:
        Formatted string for LLM prompt
    """
    output = []

    if current_pricing:
        output.append(f"CURRENT: {current_pricing['sku_name']} = ${current_pricing['retail_price']:.4f}/hr")
    else:
        output.append("CURRENT: Not available")

    if alternatives:
        # Only send top 3 alternatives to reduce token usage
        for i, alt in enumerate(alternatives[:3], 1):
            output.append(f"ALT{i}: {alt['sku_name']} = ${alt['retail_price']:.4f}/hr")
    else:
        output.append("ALTERNATIVES: None available")

    return "\n".join(output)


def format_storage_pricing_for_llm(storage_pricing: list) -> str:
    """
    Format storage pricing data for LLM context.
    Condensed format to reduce token usage.

    Args:
        storage_pricing: List of storage pricing dicts

    Returns:
        Formatted string for LLM prompt
    """
    if not storage_pricing:
        return "STORAGE PRICING: Not available"

    output = []
    # Only send top 5 options to reduce token usage
    for idx, option in enumerate(storage_pricing[:5], 1):
        output.append(f"OPT{idx}: {option['meter_name']} = {option['retail_price']:.5f}/{option['unit_of_measure']}")

    return "\n".join(output)


def format_ip_pricing_for_llm(ip_pricing: list) -> str:
    """
    Format public IP pricing data for LLM context.
    Condensed format to reduce token usage.

    Args:
        ip_pricing: List of IP pricing dicts

    Returns:
        Formatted string for LLM prompt
    """
    if not ip_pricing:
        return "PUBLIC IP PRICING: Not available"

    output = []
    # Only send top 4 options to reduce token usage
    for idx, opt in enumerate(ip_pricing[:4], 1):
        output.append(f"OPT{idx}: {opt['meter_name']} = {opt['retail_price']:.5f}/{opt['unit_of_measure']}")

    return "\n".join(output)
