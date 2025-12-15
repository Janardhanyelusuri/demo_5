"""
AWS EC2 LLM Integration
Provides cost optimization recommendations for EC2 instances
"""

import psycopg2
import pandas as pd
import sys
import os
import json
from datetime import datetime, timedelta
import logging
from psycopg2 import sql
from typing import Optional, List, Dict, Any
from sqlalchemy import create_engine
from urllib.parse import quote_plus
from dotenv import load_dotenv

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("ec2_llm_integration")

# Path adjustments for imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
from app.core.genai import llm_call
from app.ingestion.aws.postgres_operations import connection
from app.ingestion.azure.llm_json_extractor import extract_json
from app.core.task_manager import task_manager  # For cancellation support
# Import pricing helpers for dynamic pricing context
from app.ingestion.aws.pricing_helpers import (
    get_ec2_current_pricing,
    get_ec2_alternative_pricing,
    format_ec2_pricing_for_llm
)

load_dotenv()


# ============================================================
# EC2 FUNCTIONS
# ============================================================

@connection
def fetch_ec2_utilization_data(conn, schema_name, start_date, end_date, instance_id=None):
    """
    Fetch EC2 instance metrics and cost data from fact tables and FOCUS billing.
    Calculates AVG, MAX, and MAX Date for each metric.

    Args:
        conn: Database connection
        schema_name: PostgreSQL schema name
        start_date: Start date for analysis
        end_date: End date for analysis
        instance_id: Optional specific instance ID to analyze

    Returns:
        DataFrame with EC2 instance utilization and cost data
    """

    instance_filter_sql = sql.SQL("AND LOWER(m.resource_id) = LOWER(%s)") if instance_id else sql.SQL("")

    QUERY = sql.SQL("""
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
                {instance_filter}
        ),

        usage_summary AS (
            SELECT
                instance_id,
                instance_name,
                instance_type,
                region,
                account_id,
                metric_name,
                -- Convert bytes to GB for disk and network metrics
                AVG(
                    CASE
                        WHEN metric_name IN ('DiskReadBytes', 'DiskWriteBytes', 'NetworkIn', 'NetworkOut')
                        THEN metric_value / 1073741824.0  -- Convert bytes to GB (1024^3)
                        ELSE metric_value
                    END
                ) AS avg_value,
                MAX(
                    CASE
                        WHEN metric_name IN ('DiskReadBytes', 'DiskWriteBytes', 'NetworkIn', 'NetworkOut')
                        THEN metric_value / 1073741824.0  -- Convert bytes to GB (1024^3)
                        ELSE metric_value
                    END
                ) AS max_value,

                -- Get the timestamp when max value occurred
                FIRST_VALUE(timestamp) OVER (
                    PARTITION BY instance_id, metric_name
                    ORDER BY metric_value DESC, timestamp DESC
                ) AS max_date

            FROM metric_agg
            GROUP BY instance_id, instance_name, instance_type, region, account_id, metric_name
        ),

        metric_map AS (
            SELECT
                instance_id,
                instance_name,
                instance_type,
                region,
                account_id,
                -- Combine AVG, MAX value, and MAX date into JSON objects
                json_object_agg(
                    metric_name || '_Avg', ROUND(avg_value::numeric, 6)
                ) ||
                json_object_agg(
                    metric_name || '_Max', ROUND(max_value::numeric, 6)
                ) ||
                json_object_agg(
                    metric_name || '_MaxDate', TO_CHAR(max_date, 'YYYY-MM-DD HH24:MI:SS')
                ) AS metrics_json
            FROM usage_summary
            GROUP BY 1, 2, 3, 4, 5
        )

        SELECT
            us.instance_id,
            us.instance_name,
            us.instance_type,
            us.region,
            us.account_id,
            m.metrics_json,
            -- Pull cost data from FOCUS billing table
            COALESCE(SUM(ff.billed_cost), 0) AS billed_cost,
            COALESCE(SUM(ff.consumed_quantity), 0) AS consumed_quantity,
            MAX(ff.consumed_unit) AS consumed_unit,
            MAX(ff.pricing_category) AS pricing_category,
            MAX(ff.pricing_unit) AS pricing_unit,
            MAX(ff.contracted_unit_price) AS contracted_unit_price
        FROM usage_summary us
        LEFT JOIN metric_map m ON m.instance_id = us.instance_id
        LEFT JOIN {schema_name}.gold_aws_fact_focus ff
            ON LOWER(ff.resource_id) LIKE '%%' || LOWER(us.instance_id) || '%%'
               AND ff.service_name = 'Amazon Elastic Compute Cloud - Compute'
               AND ff.charge_period_start::date <= %s
               AND ff.charge_period_end::date >= %s
        GROUP BY 1, 2, 3, 4, 5, 6
    """).format(
        schema_name=sql.Identifier(schema_name),
        instance_filter=instance_filter_sql
    )

    params = [start_date, end_date, end_date, start_date]
    if instance_id:
        params.insert(2, instance_id)

    try:
        cursor = conn.cursor()
        cursor.execute(QUERY, params)

        columns = [desc[0] for desc in cursor.description]
        data = cursor.fetchall()
        cursor.close()

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data, columns=columns)

        # Expand metrics_json into separate columns
        if not df.empty and "metrics_json" in df.columns:
            metrics_expanded = pd.json_normalize(df["metrics_json"].fillna({})).add_prefix("metric_")
            metrics_expanded.index = df.index
            df = pd.concat([df.drop(columns=["metrics_json"]), metrics_expanded], axis=1)

        return df

    except psycopg2.Error as e:
        raise RuntimeError(f"PostgreSQL query failed: {e}") from e
    except Exception as e:
        raise RuntimeError(f"Unexpected error during EC2 data fetch: {e}") from e


def _estimate_ec2_hourly_cost(instance_type: str) -> float:
    """
    Estimate EC2 hourly cost based on instance type when database pricing unavailable.
    Uses realistic AWS EC2 pricing patterns.
    """
    instance_lower = instance_type.lower()

    # Extract size from instance type (e.g., t3.large -> large)
    parts = instance_type.split('.')
    size = parts[1] if len(parts) > 1 else 'medium'
    family = parts[0] if len(parts) > 0 else 't3'

    # Size multipliers
    size_multipliers = {
        'nano': 0.125, 'micro': 0.25, 'small': 0.5, 'medium': 1.0,
        'large': 2.0, 'xlarge': 4.0, '2xlarge': 8.0, '4xlarge': 16.0,
        '8xlarge': 32.0, '12xlarge': 48.0, '16xlarge': 64.0, '24xlarge': 96.0
    }
    multiplier = size_multipliers.get(size, 1.0)

    # Base cost estimates by instance family
    if family.startswith('t'):
        base_cost = 0.0104  # T-series (burstable)
    elif family.startswith('m'):
        base_cost = 0.096   # M-series (general purpose)
    elif family.startswith('c'):
        base_cost = 0.085   # C-series (compute optimized)
    elif family.startswith('r'):
        base_cost = 0.126   # R-series (memory optimized)
    elif family.startswith('x'):
        base_cost = 0.333   # X-series (extreme memory)
    elif family.startswith('i') or family.startswith('d'):
        base_cost = 0.156   # I/D-series (storage optimized)
    elif family.startswith('g') or family.startswith('p'):
        base_cost = 0.526   # G/P-series (GPU instances)
    elif family.startswith('a'):
        base_cost = 0.0102  # A-series (ARM-based)
    else:
        base_cost = 0.096   # Default estimate

    # Apply size multiplier with economies of scale
    estimated = base_cost * (multiplier ** 0.95)

    return round(estimated, 4)


def _extrapolate_costs(billed_cost: float, duration_days: int) -> Dict[str, float]:
    """Helper to calculate monthly/annual forecasts."""
    if duration_days == 0:
        return {"monthly": 0.0, "annually": 0.0}

    avg_daily_cost = billed_cost / duration_days
    print(f"Avg daily cost calculated: {avg_daily_cost}")
    # Use 30.4375 for average days in a month (365.25 / 12)
    monthly = avg_daily_cost * 30.4375
    annually = avg_daily_cost * 365
    print(f"Extrapolated monthly: {monthly}, annually: {annually}")
    return {"monthly": monthly, "annually": annually}


def generate_ec2_prompt(instance_data: Dict[str, Any], monthly_forecast: float, annual_forecast: float) -> str:
    """
    Generate LLM prompt for EC2 instance optimization recommendations with pricing context.

    Args:
        instance_data: Dictionary containing instance metrics and cost data
        monthly_forecast: Extrapolated monthly cost forecast
        annual_forecast: Extrapolated annual cost forecast

    Returns:
        Formatted prompt string for the LLM
    """

    instance_id = instance_data.get('instance_id', 'Unknown')
    instance_type = instance_data.get('instance_type', 'Unknown')
    region = instance_data.get('region', 'Unknown')
    billed_cost = instance_data.get('billed_cost', 0)

    # Extract metrics
    cpu_avg = instance_data.get('metric_CPUUtilization_Avg', 0)
    cpu_max = instance_data.get('metric_CPUUtilization_Max', 0)
    cpu_max_date = instance_data.get('metric_CPUUtilization_MaxDate', 'N/A')

    network_in_avg = instance_data.get('metric_NetworkIn_Avg', 0)
    network_in_max = instance_data.get('metric_NetworkIn_Max', 0)
    network_in_max_date = instance_data.get('metric_NetworkIn_MaxDate', 'N/A')

    network_out_avg = instance_data.get('metric_NetworkOut_Avg', 0)
    network_out_max = instance_data.get('metric_NetworkOut_Max', 0)
    network_out_max_date = instance_data.get('metric_NetworkOut_MaxDate', 'N/A')

    disk_read_avg = instance_data.get('metric_DiskReadOps_Avg', 0)
    disk_read_max = instance_data.get('metric_DiskReadOps_Max', 0)
    disk_read_max_date = instance_data.get('metric_DiskReadOps_MaxDate', 'N/A')

    disk_write_avg = instance_data.get('metric_DiskWriteOps_Avg', 0)
    disk_write_max = instance_data.get('metric_DiskWriteOps_Max', 0)
    disk_write_max_date = instance_data.get('metric_DiskWriteOps_MaxDate', 'N/A')

    start_date = instance_data.get('start_date', 'N/A')
    end_date = instance_data.get('end_date', 'N/A')
    duration_days = instance_data.get('duration_days', 0)

    # Fetch pricing data from database
    schema_name = instance_data.get('schema_name', '')

    alternative_pricing = []
    pricing_context = ""
    has_pricing = False
    estimated_hours = 0  # Initialize
    current_hourly_rate = 0  # Initialize

    if schema_name and instance_type and instance_type != 'Unknown':
        try:
            # Only fetch DIVERSE alternative instance types (4 total: diverse families)
            alternative_pricing = get_ec2_alternative_pricing(schema_name, instance_type, region, max_results=4)

            # Debug: Print fetched pricing
            print(f"\n{'='*60}")
            print(f"PRICING DEBUG - AWS EC2: {instance_type} in {region}")
            print(f"{'='*60}")

            # Calculate implied hourly rate to estimate usage hours
            if billed_cost > 0 and duration_days > 0:
                estimated_market_rate = _estimate_ec2_hourly_cost(instance_type)
                if estimated_market_rate > 0:
                    estimated_hours_total = billed_cost / estimated_market_rate
                    hours_per_day = estimated_hours_total / duration_days
                    estimated_hours = hours_per_day * 30.4375  # Monthly hours
                    current_hourly_rate = estimated_market_rate
                    has_pricing = True
                    print(f"CURRENT: {instance_type}")
                    print(f"  Estimated rate: {estimated_market_rate:.4f}/hr")
                    print(f"  Usage: {estimated_hours:.2f}hrs/mo (from {billed_cost:.4f} / {estimated_market_rate:.4f})")
            else:
                estimated_hours = 0
                current_hourly_rate = 0

            if alternative_pricing and len(alternative_pricing) > 0:
                print(f"\nDIVERSE INSTANCE FAMILIES:")
                for idx, alt in enumerate(alternative_pricing, 1):
                    print(f"  {idx}. {alt['instance_type']} ({alt['vcpu']}vCPU, {alt['memory']}): {alt['price_per_hour']:.4f}/hr")
                has_pricing = True
            else:
                print(f"\nALTERNATIVE INSTANCES: Not found in database")
                print(f"Using fallback pricing estimates")

                # Fallback: Diverse instance families
                estimated_base = estimated_market_rate if estimated_market_rate > 0 else 0.05
                alternative_pricing = [
                    {'instance_type': 't3.medium (General Purpose/Burstable)', 'price_per_hour': estimated_base * 0.4, 'vcpu': '2', 'memory': '4 GiB'},
                    {'instance_type': 'm5.large (General Purpose)', 'price_per_hour': estimated_base * 0.8, 'vcpu': '2', 'memory': '8 GiB'},
                    {'instance_type': 'c5.large (Compute Optimized)', 'price_per_hour': estimated_base * 0.9, 'vcpu': '2', 'memory': '4 GiB'},
                    {'instance_type': 'r5.large (Memory Optimized)', 'price_per_hour': estimated_base * 1.2, 'vcpu': '2', 'memory': '16 GiB'},
                ]
                has_pricing = True

                for alt in alternative_pricing:
                    print(f"  {alt['instance_type']}: {alt['price_per_hour']:.4f}/hr (estimated)")

            print(f"{'='*60}\n")

            # Format pricing for LLM
            pricing_context = format_ec2_pricing_for_llm(alternative_pricing)
        except Exception as e:
            LOG.warning(f"⚠️ Error fetching EC2 pricing data: {e}")
            import traceback
            traceback.print_exc()

            # Fallback pricing on error
            estimated_hourly = _estimate_ec2_hourly_cost(instance_type)
            current_hourly_rate = estimated_hourly
            has_pricing = True
            # Calculate estimated hours from fallback pricing
            if current_hourly_rate > 0:
                estimated_hours_total = billed_cost / current_hourly_rate
                estimated_hours = (estimated_hours_total / duration_days) * 30.4375

            # Fallback alternatives with diverse families
            alternative_pricing = [
                {'instance_type': 't3.medium', 'price_per_hour': estimated_hourly * 0.4, 'vcpu': '2', 'memory': '4 GiB'},
                {'instance_type': 'm5.large', 'price_per_hour': estimated_hourly * 0.8, 'vcpu': '2', 'memory': '8 GiB'},
            ]
            pricing_context = format_ec2_pricing_for_llm(alternative_pricing)
    else:
        pricing_context = "EC2 ALTERNATIVES: Not available (schema or instance type not provided)"

    # Check if we have metrics available
    has_metrics = (cpu_avg > 0 or cpu_max > 0 or network_in_avg > 0 or
                   network_out_avg > 0 or disk_read_avg > 0 or disk_write_avg > 0)

    # Guard clause for missing data
    if not has_pricing or not has_metrics or instance_type == "Unknown" or instance_type == "None":
        return f"""AWS EC2 {instance_id} | {instance_type} | {duration_days}d | {billed_cost:.2f}
ISSUE: {'No pricing' if not has_pricing else 'No metrics' if not has_metrics else 'Unknown type'}
OUTPUT: {{"recommendations": {{"effective_recommendation": {{"text": "Cannot recommend", "explanation": "Insufficient data", "saving_pct": 0}}, "additional_recommendation": [], "base_of_recommendations": []}}, "cost_forecasting": {{"monthly": {monthly_forecast:.2f}, "annually": {annual_forecast:.2f}}}, "anomalies": [], "contract_deal": {{"assessment": "unknown", "for_sku": "{instance_type}", "reason": "Insufficient data", "monthly_saving_pct": 0, "annual_saving_pct": 0}}}}"""

    # Prepare full resource data for LLM to analyze
    resource_data_str = f"""RESOURCE: {instance_type} in {region}
PERIOD: {duration_days} days
BILLED_COST: {billed_cost:.4f}
MONTHLY_FORECAST: {monthly_forecast:.2f}
ANNUAL_FORECAST: {annual_forecast:.2f}
ESTIMATED_USAGE: {estimated_hours:.2f} hours/month (based on actual usage pattern from billed cost)
CURRENT_RATE: {current_hourly_rate:.4f}/hour (estimated from instance type)

METRICS:
- CPU: Avg={cpu_avg:.2f}%, Max={cpu_max:.2f}%, MaxDate={cpu_max_date}
- Network In: Avg={network_in_avg:.2f}GB, Max={network_in_max:.2f}GB, MaxDate={network_in_max_date}
- Network Out: Avg={network_out_avg:.2f}GB, Max={network_out_max:.2f}GB, MaxDate={network_out_max_date}
- Disk Read: Avg={disk_read_avg:.2f}ops/sec, Max={disk_read_max:.2f}ops/sec, MaxDate={disk_read_max_date}
- Disk Write: Avg={disk_write_avg:.2f}ops/sec, Max={disk_write_max:.2f}ops/sec, MaxDate={disk_write_max_date}

ALTERNATIVE_INSTANCES:
{pricing_context}
"""

    prompt = f"""Analyze this AWS EC2 instance and provide cost optimization recommendations.

{resource_data_str}

INSTRUCTIONS:
1. Analyze all resource data above (metrics, usage patterns, costs)
2. ESTIMATED_USAGE = {estimated_hours:.2f} hrs/month is calculated from actual billed cost (implied usage pattern)
3. For each recommendation:
   - First explain WHY (theoretical analysis of metrics and usage patterns)
   - Then show calculations: Use ESTIMATED_USAGE ({estimated_hours:.2f} hrs/month) for all cost projections
   - Example: Current monthly = CURRENT_RATE × {estimated_hours:.2f}, Alternative monthly = ALT_RATE × {estimated_hours:.2f}
4. Use actual instance type names (e.g., "{instance_type}", not "current")
5. Only recommend if it saves money (positive savings). If a recommendation doesn't save money, use saving_pct: 0
6. Each recommendation must be a DIFFERENT type of action:
   - Consider: instance type resize, Reserved Instance/Savings Plan, spot instance, usage schedule, optimization
   - Pick the ones that make sense for THIS resource's specific data
7. For base_of_recommendations: select the metrics YOU used to make your decision - MUST include metric name AND value (e.g., "CPUUtilization: 45.2%", "NetworkIn: 1.5GB")
8. For contract_deal: analyze if RI/Savings Plan makes sense for THIS usage pattern (consistent high usage >500hrs/mo = good, low/sporadic <200hrs/mo = bad)
9. CRITICAL: saving_pct MUST ALWAYS be a NUMBER (integer or decimal), NEVER a string like "unknown". Use 0 if savings cannot be calculated.

OUTPUT FORMAT (JSON):
{{
  "recommendations": {{
    "effective_recommendation": {{"text": "action description", "explanation": "theoretical WHY + calculation MATH", "saving_pct": 0}},
    "additional_recommendation": [
      {{"text": "action description", "explanation": "theoretical WHY + calculation MATH", "saving_pct": 0}}
    ],
    "base_of_recommendations": ["metric_name: value with unit", "metric_name: value with unit"]
  }},
  "cost_forecasting": {{"monthly": {monthly_forecast:.2f}, "annually": {annual_forecast:.2f}}},
  "anomalies": [{{"metric_name": "name", "timestamp": "date", "value": 0, "reason_short": "why unusual"}}],
  "contract_deal": {{"assessment": "good|bad|unknown", "for_sku": "{instance_type}", "reason": "theoretical analysis of usage pattern", "monthly_saving_pct": 0, "annual_saving_pct": 0}}
}}

REMINDER: All numeric fields (saving_pct, value, monthly_saving_pct, annual_saving_pct) must be NUMBERS, not strings."""

    return prompt


def get_ec2_recommendation_single(instance_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Get LLM recommendation for a single EC2 instance.

    Args:
        instance_data: Dictionary containing instance metrics

    Returns:
        Dictionary containing recommendations or None if error
    """
    try:
        # Calculate cost forecasts
        billed_cost = instance_data.get('billed_cost', 0.0)
        duration_days = int(instance_data.get('duration_days', 30) or 30)
        forecast = _extrapolate_costs(billed_cost, duration_days)

        # Generate prompt with forecasts
        prompt = generate_ec2_prompt(instance_data, forecast['monthly'], forecast['annually'])
        llm_response = llm_call(prompt)

        if not llm_response:
            LOG.warning(f"Empty LLM response for instance {instance_data.get('instance_id')}")
            return None

        # Extract and parse JSON from LLM response
        recommendation = extract_json(llm_response)

        if recommendation:
            # Add resource_id and forecasts to the recommendation
            recommendation['resource_id'] = instance_data.get('instance_id', 'Unknown')
            recommendation['_forecast_monthly'] = forecast['monthly']
            recommendation['_forecast_annual'] = forecast['annually']
            return recommendation
        else:
            LOG.warning(f"Failed to parse JSON for instance {instance_data.get('instance_id')}")
            return None

    except Exception as e:
        LOG.error(f"Error getting EC2 recommendation: {e}")
        return None


def run_llm_analysis_ec2(resource_type: str, schema_name: str,
                         start_date: Optional[datetime] = None,
                         end_date: Optional[datetime] = None,
                         resource_id: Optional[str] = None,
                         task_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Main entry point for EC2 LLM analysis with cancellation support.

    Args:
        resource_type: Should be 'ec2'
        schema_name: PostgreSQL schema name
        start_date: Start date for analysis
        end_date: End date for analysis
        resource_id: Optional specific instance ID
        task_id: Optional task ID for cancellation support

    Returns:
        List of recommendation dictionaries
    """

    start_str = start_date.strftime("%Y-%m-%d") if start_date else (datetime.utcnow().date() - timedelta(days=30)).strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d") if end_date else datetime.utcnow().date().strftime("%Y-%m-%d")

    LOG.info(f"🚀 Starting EC2 LLM analysis from {start_str} to {end_str}...")

    try:
        df = fetch_ec2_utilization_data(schema_name, start_str, end_str, resource_id)
    except RuntimeError as e:
        LOG.error(f"❌ Failed to fetch EC2 utilization data: {e}")
        return []
    except Exception as e:
        LOG.error(f"❌ Unhandled error during data fetching: {e}")
        return []

    if df is None or df.empty:
        LOG.warning("⚠️ No EC2 instance data found for the requested date range / instance.")
        return []

    LOG.info(f"📈 Retrieved data for {len(df)} EC2 instance(s)")

    # Annotate with date info for LLM context
    df["start_date"] = start_str
    df["end_date"] = end_str
    df["duration_days"] = (pd.to_datetime(end_str) - pd.to_datetime(start_str)).days

    # Convert to list of dicts
    instances = df.to_dict(orient="records")
    total_instances = len(instances)

    LOG.info("🤖 Calling LLM for EC2 recommendations...")
    recommendations = []

    for idx, instance_data in enumerate(instances):
        # Check if task has been cancelled
        if task_id:
            is_cancelled = task_manager.is_cancelled(task_id)
            if is_cancelled:
                LOG.info(f"🛑 Task {task_id} was cancelled. Stopping EC2 analysis. Processed {len(recommendations)}/{total_instances}")
                break
            # Debug: Confirm task is still active
            if idx == 0 or idx % 10 == 0:  # Log every 10th iteration
                LOG.info(f"  🔍 Task {task_id[:8]}... still running, not cancelled (instance {idx + 1}/{total_instances})")

        instance_id = instance_data.get('resource_id', 'Unknown')
        LOG.info(f"  [{idx + 1}/{total_instances}] Processing instance: {instance_id}")

        # Add schema_name and region for pricing lookups
        instance_data['schema_name'] = schema_name
        instance_data['region'] = instance_data.get('region', 'us-east-1')
        rec = get_ec2_recommendation_single(instance_data)
        if rec:
            recommendations.append(rec)

    if recommendations:
        LOG.info(f"✅ EC2 analysis complete! Generated {len(recommendations)} recommendation(s).")
        return recommendations
    else:
        LOG.warning("⚠️ No recommendations generated by LLM.")
        return []


# ============================================================

# Wrapper function for backward compatibility
def run_llm_analysis(resource_type: str, schema_name: str,
                     start_date: str, end_date: str,
                     resource_id: Optional[str] = None,
                     task_id: Optional[str] = None):
    """
    Main entry point for EC2 LLM analysis.
    Wrapper function that calls run_llm_analysis_ec2.
    """
    return run_llm_analysis_ec2(resource_type, schema_name, start_date, end_date, resource_id, task_id)
