# app/ingestion/azure/llm_analysis.py

import json
import logging
from typing import Optional, List, Dict, Any
import sys
import os

# Set up basic logging configuration
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')

# Assuming app.core.genai and app.ingestion.azure.llm_json_extractor are available
# Ensure this path manipulation is correct for your environment structure
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../')))
from app.core.genai import llm_call
from app.ingestion.azure.llm_json_extractor import extract_json_str
# Import pricing helpers for dynamic pricing context
from app.ingestion.azure.pricing_helpers import (
    get_vm_current_pricing,
    get_vm_alternative_pricing,
    get_storage_pricing_context,
    get_public_ip_pricing_context,
    format_vm_pricing_for_llm,
    format_storage_pricing_for_llm,
    format_ip_pricing_for_llm
)

# --- Utility Functions ---

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
    return {
        "monthly": round(monthly, 2),
        "annually": round(annually, 2)
    }

def _format_metrics_for_llm(resource_data: dict, resource_type: str = "vm") -> Dict[str, Any]:
    """
    Groups and formats RELEVANT metric data (AVG, MAX, MaxDate) for the LLM.
    Filters to only cost-relevant metrics to reduce token usage.
    Adds unit clarification for metrics converted from bytes to GB.

    Args:
        resource_data: Dict containing metric data with keys like "metric_X_Avg"
        resource_type: "vm" or "storage" to determine which metrics to include
    """
    # Define relevant metrics for each resource type (to reduce token usage)
    RELEVANT_METRICS = {
        "vm": [
            "Percentage CPU",
            "Available Memory Bytes",
            "Available Memory",
            "Disk Read Operations/Sec",
            "Disk Write Operations/Sec",
            "Network In Total",
            "Network Out Total",
            "OS Disk IOPS Consumed Percentage",
            "Data Disk IOPS Consumed Percentage"
        ],
        "storage": [
            "UsedCapacity",
            "Transactions",
            "Ingress",
            "Egress",
            "Availability",
            "SuccessServerLatency",
            "BlobCapacity",
            "FileCapacity",
            "TableCapacity",
            "QueueCapacity"
        ],
        "publicip": [
            "PacketCount",
            "ByteCount",
            "VipAvailability",
            "SynCount",
            "TCPBytesForwardedDDoS",
            "TCPBytesInDDoS",
            "UDPBytesForwardedDDoS",
            "UDPBytesInDDoS"
        ]
    }

    # Metrics that have been converted from bytes to GB (for display name correction)
    BYTE_TO_GB_METRICS = {
        # VM metrics
        "Available Memory Bytes": "Available Memory (GB)",
        "Network In": "Network In (GB)",
        "Network Out": "Network Out (GB)",
        "Network In Total": "Network In Total (GB)",
        "Network Out Total": "Network Out Total (GB)",
        # Storage metrics
        "UsedCapacity": "Used Capacity (GB)",
        "BlobCapacity": "Blob Capacity (GB)",
        "FileCapacity": "File Capacity (GB)",
        "TableCapacity": "Table Capacity (GB)",
        "QueueCapacity": "Queue Capacity (GB)",
        "Ingress": "Ingress (GB)",
        "Egress": "Egress (GB)",
        # Public IP metrics
        "ByteCount": "Byte Count (GB)",
        "TCPBytesForwardedDDoS": "TCP Bytes Forwarded DDoS (GB)",
        "TCPBytesInDDoS": "TCP Bytes In DDoS (GB)",
        "UDPBytesForwardedDDoS": "UDP Bytes Forwarded DDoS (GB)",
        "UDPBytesInDDoS": "UDP Bytes In DDoS (GB)"
    }

    relevant_metric_names = RELEVANT_METRICS.get(resource_type, [])
    formatted_metrics = {}

    # Identify unique metric names (e.g., "Percentage CPU" from "metric_Percentage CPU_Avg")
    unique_metric_names = set(
        k.replace("metric_", "").rsplit('_', 1)[0]
        for k in resource_data.keys()
        if k.startswith("metric_") and len(k.split('_')) > 2
    )

    for metric_name in unique_metric_names:
        # Only include if in relevant list OR if we don't have a filter for this resource type
        if relevant_metric_names and metric_name not in relevant_metric_names:
            continue

        # Reconstruct the full keys
        avg_key = f"metric_{metric_name}_Avg"
        max_key = f"metric_{metric_name}_Max"
        date_key = f"metric_{metric_name}_MaxDate"

        # Build the structured entry for the LLM
        entry = {
            "Avg": resource_data.get(avg_key),
            "Max": resource_data.get(max_key),
            "MaxDate": resource_data.get(date_key)
        }

        # Only include if at least one value is present and not None
        if any(v is not None for v in entry.values()):
            # Use corrected display name if metric was converted to GB
            display_name = BYTE_TO_GB_METRICS.get(metric_name, metric_name)
            formatted_metrics[display_name] = entry

    return formatted_metrics

# --- PROMPT GENERATION FUNCTIONS (Updated for dynamic metric inclusion) ---

def _generate_storage_prompt(resource_data: dict, start_date: str, end_date: str, monthly_forecast: float, annual_forecast: float) -> str:
    """Generates the structured prompt for Storage LLM analysis with dynamically included metrics and pricing."""

    # Prepare the structured metrics for the prompt (only storage-relevant metrics)
    formatted_metrics = _format_metrics_for_llm(resource_data, resource_type="storage")
    current_sku = resource_data.get("sku", "N/A")
    current_tier = resource_data.get("access_tier", "N/A")
    billed_cost = resource_data.get("billed_cost", 0.0)

    # Fetch pricing data from database
    schema_name = resource_data.get("schema_name", "")
    region = resource_data.get("region", "eastus")

    pricing_context = ""
    if schema_name:
        try:
            # Get storage pricing context for different tiers
            storage_pricing = get_storage_pricing_context(schema_name, region)

            # Debug: Print fetched pricing
            print(f"\n{'='*60}")
            print(f"PRICING DEBUG - Azure Storage: {current_sku} {current_tier} in {region}")
            print(f"{'='*60}")

            if storage_pricing and len(storage_pricing) > 0:
                print(f"CURRENT TIER: {current_tier}")
                print(f"\nSTORAGE PRICING OPTIONS (Top 5):")
                for idx, (tier, info) in enumerate(list(storage_pricing.items())[:5], 1):
                    marker = "← CURRENT" if current_tier.lower() in info['meter_name'].lower() else ""
                    print(f"  {idx}. {info['meter_name']}: {info['retail_price']:.6f} per {info['unit_of_measure']} {marker}")
            else:
                print(f"CURRENT TIER: {current_tier}")
                print(f"STORAGE PRICING: Not found in database")
                print(f"Using fallback pricing estimates")

                # Fallback pricing for common storage tiers
                storage_pricing = {
                    'Hot': {'meter_name': 'Hot Tier Data Stored', 'retail_price': 0.0184, 'unit_of_measure': 'GB'},
                    'Cool': {'meter_name': 'Cool Tier Data Stored', 'retail_price': 0.0115, 'unit_of_measure': 'GB'},
                    'Archive': {'meter_name': 'Archive Tier Data Stored', 'retail_price': 0.002, 'unit_of_measure': 'GB'},
                    'Premium': {'meter_name': 'Premium LRS Data Stored', 'retail_price': 0.15, 'unit_of_measure': 'GB'}
                }

                print(f"  Hot: 0.0184 per GB (estimated)")
                print(f"  Cool: 0.0115 per GB (estimated)")
                print(f"  Archive: 0.002 per GB (estimated)")
                print(f"  (Fallback estimates - actual pricing unavailable)")

            print(f"{'='*60}\n")

            # Format pricing for LLM
            pricing_context = "\n\n" + format_storage_pricing_for_llm(storage_pricing) + "\n"
        except Exception as e:
            print(f"⚠️ Error fetching Storage pricing data: {e}")
            import traceback
            traceback.print_exc()

            # Fallback pricing on error
            pricing_context = f"\n\nPRICING DATA (ESTIMATED - Database unavailable):\nCurrent tier: {current_tier}\nHot: ~0.0184 USD/GB, Cool: ~0.0115 USD/GB, Archive: ~0.002 USD/GB\n"
    else:
        pricing_context = "\n\nPRICING DATA: Not available (schema not provided)\n"

    # Check if we have pricing and metrics available
    has_pricing = pricing_context and "Not available" not in pricing_context
    has_metrics = bool(formatted_metrics)

    # Extract key metrics for base_of_recommendations (with units)
    metrics_list = []
    for metric_name, values in formatted_metrics.items():
        if values.get('Avg') is not None:
            # Extract unit from metric name if it has one
            if 'Capacity' in metric_name or 'Used' in metric_name or 'GB' in metric_name:
                unit = 'GB'
            elif 'Transactions' in metric_name or 'Operations' in metric_name:
                unit = 'count'
            elif 'Latency' in metric_name:
                unit = 'ms'
            elif 'Availability' in metric_name or 'Percentage' in metric_name:
                unit = '%'
            else:
                unit = ''

            if unit:
                metrics_list.append(f'"{metric_name}: Avg={values["Avg"]:.2f}{unit}, Max={values.get("Max", 0):.2f}{unit}"')
            else:
                metrics_list.append(f'"{metric_name}: Avg={values["Avg"]:.2f}, Max={values.get("Max", 0):.2f}"')

    # Format as JSON array string for LLM
    metrics_list_str = '[' + ', '.join(metrics_list) + ']' if metrics_list else '[]'

    # Build explicit metric summary for the prompt (with units)
    metrics_summary = []
    for metric_name, values in formatted_metrics.items():
        if values.get('Avg') is not None:
            if 'Capacity' in metric_name or 'Used' in metric_name or 'GB' in metric_name:
                unit = 'GB'
            elif 'Transactions' in metric_name or 'Operations' in metric_name:
                unit = 'count'
            elif 'Latency' in metric_name:
                unit = 'ms'
            elif 'Availability' in metric_name or 'Percentage' in metric_name:
                unit = '%'
            else:
                unit = ''

            if unit:
                metrics_summary.append(f"- {metric_name}: Avg={values['Avg']:.2f}{unit}, Max={values.get('Max', 0):.2f}{unit}, MaxDate={values.get('MaxDate', 'N/A')}")
            else:
                metrics_summary.append(f"- {metric_name}: Avg={values['Avg']:.2f}, Max={values.get('Max', 0):.2f}, MaxDate={values.get('MaxDate', 'N/A')}")

    metrics_text = "\n".join(metrics_summary) if metrics_summary else "No metrics available"
    duration_days = resource_data.get("duration_days", 30)

    # Guard clause for missing data
    if not has_pricing or not has_metrics or current_sku == "N/A" or current_sku == "None":
        return f"""Azure Storage FinOps. Analyze {resource_data.get("resource_id", "N/A")} | SKU: {current_sku} {current_tier} | {start_date} to {end_date} ({duration_days}d) | Cost: ${billed_cost:.2f}

AVAILABLE METRICS:
{metrics_text}

PRICING OPTIONS:
{pricing_context}

ISSUE: {'Missing pricing data' if not has_pricing else 'Missing metrics data' if not has_metrics else 'Unknown SKU'}

OUTPUT (JSON only):
{{
  "recommendations": {{
    "effective_recommendation": {{"text": "Unable to provide recommendations", "explanation": "{'Pricing data unavailable for tier analysis' if not has_pricing else 'No metrics available for resource analysis' if not has_metrics else 'SKU information not available'}", "saving_pct": 0}},
    "additional_recommendation": [],
    "base_of_recommendations": {metrics_list_str}
  }},
  "cost_forecasting": {{"monthly": {monthly_forecast:.2f}, "annually": {annual_forecast:.2f}}},
  "anomalies": [],
  "contract_deal": {{"assessment": "unknown", "for_sku": "{current_sku} {current_tier}", "reason": "Insufficient data for assessment", "monthly_saving_pct": 0, "annual_saving_pct": 0}}
}}"""

    return f"""Azure Storage FinOps. Analyze {resource_data.get("resource_id", "N/A")} | {current_sku} {current_tier} | {start_date} to {end_date} ({duration_days}d)
Current Cost: ${billed_cost:.2f} for {duration_days}d | Forecast: ${monthly_forecast:.2f}/mo, ${annual_forecast:.2f}/yr

AVAILABLE METRICS (MUST USE):
{metrics_text}

PRICING OPTIONS:
{pricing_context}

RULES:
1. MUST cite exact metrics above with units (e.g., "Capacity: Avg=150.2GB, Max=200.5GB")
2. MUST use monthly forecast ${monthly_forecast:.2f} for savings calculations
3. PRICING shows storage costs per GB. Current forecast ${monthly_forecast:.2f} includes actual storage usage.
   To calculate alternative tier costs: use same storage capacity with alternative tier pricing
4. Each recommendation MUST be unique (tier change vs lifecycle policy vs replication config vs cleanup)
5. Calculate: savings_pct = ((current_monthly_forecast - alternative_tier_cost) / current_monthly_forecast) * 100
6. Anomalies: MUST use exact MaxDate from metrics, explain why value is unusual
7. Contract assessment: Compare current monthly cost vs alternatives, explain good/bad

OUTPUT (JSON only, NO markdown):
{{
  "recommendations": {{
    "effective_recommendation": {{
      "text": "Primary action with exact tier name from PRICING",
      "explanation": "Based on [cite exact metrics with units from AVAILABLE METRICS] over {duration_days} days, current monthly forecast is ${monthly_forecast:.2f}. [Specific tier from PRICING] costs [exact price]/mo for [capacity]GB, saving [exact amount].",
      "saving_pct": <number calculated from monthly forecast>
    }},
    "additional_recommendation": [
      {{
        "text": "DIFFERENT recommendation type (e.g., Lifecycle policy to archive old data)",
        "explanation": "With [cite capacity metrics], moving data older than 90 days to Archive tier at [price from PRICING] saves [amount]/mo.",
        "saving_pct": <number>
      }},
      {{
        "text": "THIRD unique recommendation (e.g., Change replication to LRS if RA-GRS not needed)",
        "explanation": "Given [usage pattern from metrics], reducing replication from GRS to LRS saves ~50% on storage costs.",
        "saving_pct": <number>
      }}
    ],
    "base_of_recommendations": {metrics_list_str}
  }},
  "cost_forecasting": {{"monthly": {monthly_forecast:.2f}, "annually": {annual_forecast:.2f}}},
  "anomalies": [
    {{"metric_name": "Exact name from AVAILABLE METRICS", "timestamp": "Exact MaxDate from metrics", "value": <exact Max value from metrics>, "reason_short": "Explain why this max value is unusual (spike/drop/sustained high)"}},
    {{"metric_name": "Another metric name", "timestamp": "Its MaxDate", "value": <its Max value>, "reason_short": "Why unusual for this resource type"}}
  ],
  "contract_deal": {{
    "assessment": "good|bad|unknown",
    "for_sku": "{current_sku} {current_tier}",
    "reason": "Current monthly forecast ${monthly_forecast:.2f} vs [specific alternative tier from PRICING] at [price]/mo for same capacity = [comparison]. {'Good deal if current is cheaper' if billed_cost < monthly_forecast else 'Bad deal if overpaying'}",
    "monthly_saving_pct": <number>,
    "annual_saving_pct": <number>
  }}
}}"""

def _estimate_vm_hourly_cost(sku_name: str) -> float:
    """
    Estimate VM hourly cost based on SKU name when database pricing unavailable.
    Uses realistic Azure VM pricing patterns.
    """
    sku_lower = sku_name.lower()

    # Extract size from SKU (e.g., Standard_D4s_v3 -> 4)
    import re
    size_match = re.search(r'(\d+)', sku_name)
    size = int(size_match.group(1)) if size_match else 2

    # Base cost estimates by VM series
    if 'b' in sku_lower or 'burstable' in sku_lower:
        base_cost = 0.02  # B-series (burstable)
    elif 'd' in sku_lower:
        base_cost = 0.096  # D-series (general purpose)
    elif 'e' in sku_lower:
        base_cost = 0.126  # E-series (memory optimized)
    elif 'f' in sku_lower:
        base_cost = 0.095  # F-series (compute optimized)
    elif 'a' in sku_lower:
        base_cost = 0.085  # A-series (ARM-based)
    elif 'nv' in sku_lower or 'nc' in sku_lower:
        base_cost = 0.90  # N-series (GPU)
    elif 'm' in sku_lower:
        base_cost = 0.250  # M-series (memory optimized large)
    else:
        base_cost = 0.10  # Default estimate

    # Scale by size (roughly linear but with volume discount)
    estimated = base_cost * (size ** 0.9)

    # Premium features (SSD, v3/v4/v5 generations)
    if 's' in sku_lower:
        estimated *= 1.15  # Premium SSD support
    if 'v5' in sku_lower:
        estimated *= 1.10  # Latest generation
    elif 'v4' in sku_lower:
        estimated *= 1.05
    elif 'v3' in sku_lower:
        estimated *= 1.02

    return round(estimated, 4)

def _generate_compute_prompt(resource_data: dict, start_date: str, end_date: str, monthly_forecast: float, annual_forecast: float) -> str:
    """Generates the structured prompt for Compute/VM LLM analysis with dynamically included metrics and pricing."""

    # Prepare the structured metrics for the prompt (only VM-relevant metrics)
    formatted_metrics = _format_metrics_for_llm(resource_data, resource_type="vm")
    current_sku = resource_data.get("instance_type", "N/A")
    billed_cost = resource_data.get("billed_cost", 0.0)
    resource_id = resource_data.get("resource_id", "N/A")
    duration_days = resource_data.get("duration_days", 30)

    # Fetch pricing data from database
    schema_name = resource_data.get("schema_name", "")
    region = resource_data.get("region", "eastus")

    pricing_context = ""
    estimated_hours = 0  # Initialize
    current_hourly_rate = 0  # Initialize

    if schema_name and current_sku and current_sku != "N/A":
        try:
            # Get current SKU pricing
            current_pricing = get_vm_current_pricing(schema_name, current_sku, region)

            # Get alternative SKU pricing (reduced from 10 to 5 to avoid rate limits)
            alternative_pricing = get_vm_alternative_pricing(schema_name, current_sku, region, max_results=5)

            # Debug: Print fetched pricing
            print(f"\n{'='*60}")
            print(f"PRICING DEBUG - Azure VM: {current_sku} in {region}")
            print(f"{'='*60}")

            if current_pricing:
                print(f"CURRENT SKU PRICING:")
                print(f"  SKU: {current_pricing.get('sku_name')}")
                print(f"  Hourly: {current_pricing.get('retail_price')}")
                print(f"  Monthly: {current_pricing.get('monthly_cost')}")
                print(f"  Currency: {current_pricing.get('currency_code', 'N/A')}")

                if alternative_pricing:
                    print(f"\nALTERNATIVE SKUs (Top 5):")
                    for idx, alt in enumerate(alternative_pricing[:5], 1):
                        savings = current_pricing['monthly_cost'] - alt['monthly_cost']
                        savings_pct = (savings / current_pricing['monthly_cost']) * 100 if current_pricing['monthly_cost'] > 0 else 0
                        print(f"  {idx}. {alt['sku_name']}: {alt['price_per_hour']}/hr ({alt['monthly_cost']}/mo) - Save {savings_pct:.1f}%")
                else:
                    print(f"\nALTERNATIVE SKUs: Not found in database")
            else:
                print(f"CURRENT SKU PRICING: Not found in database")
                print(f"Using fallback pricing estimates")

                # Fallback pricing when database pricing not available
                # Estimate based on typical Azure VM pricing patterns
                estimated_hourly = _estimate_vm_hourly_cost(current_sku)
                current_pricing = {
                    'sku_name': current_sku,
                    'retail_price': estimated_hourly,
                    'monthly_cost': estimated_hourly * 730,
                    'currency_code': 'USD',
                    'unit_of_measure': '1 Hour'
                }

                # Estimate alternatives (smaller SKUs typically 50% and 75% of current)
                # IMPORTANT: Use 'retail_price' for Azure (not 'price_per_hour')
                alternative_pricing = [
                    {'sku_name': f'{current_sku.split("_")[0]}_Smaller1', 'retail_price': estimated_hourly * 0.5, 'monthly_cost': estimated_hourly * 0.5 * 730},
                    {'sku_name': f'{current_sku.split("_")[0]}_Smaller2', 'retail_price': estimated_hourly * 0.75, 'monthly_cost': estimated_hourly * 0.75 * 730},
                    {'sku_name': f'{current_sku.split("_")[0]}_Larger1', 'retail_price': estimated_hourly * 1.5, 'monthly_cost': estimated_hourly * 1.5 * 730}
                ]

                print(f"  Estimated hourly: {estimated_hourly:.4f} USD")
                print(f"  Estimated monthly: {current_pricing['monthly_cost']:.2f} USD")
                print(f"  (Fallback estimates - actual pricing unavailable)")

            print(f"{'='*60}\n")

            # Calculate estimated usage hours for clearer alternative cost calculations
            if current_pricing and current_pricing.get('retail_price', 0) > 0:
                current_hourly_rate = current_pricing['retail_price']
                estimated_hours = billed_cost / current_hourly_rate
                print(f"Estimated usage hours: {estimated_hours:.2f} hours over {duration_days} days")
                print(f"Calculated from: ${billed_cost:.4f} / ${current_hourly_rate:.4f}/hr")
            else:
                estimated_hours = 0
                current_hourly_rate = 0

            # Format pricing for LLM
            pricing_context = "\n\n" + format_vm_pricing_for_llm(current_pricing, alternative_pricing) + "\n"
        except Exception as e:
            print(f"⚠️ Error fetching VM pricing data: {e}")
            import traceback
            traceback.print_exc()

            # Fallback pricing on error - create both current and alternatives
            estimated_hourly = _estimate_vm_hourly_cost(current_sku)
            current_hourly_rate = estimated_hourly
            current_pricing = {
                'sku_name': current_sku,
                'retail_price': estimated_hourly,
                'monthly_cost': estimated_hourly * 730,
                'currency_code': 'USD',
                'unit_of_measure': '1 Hour'
            }
            alternative_pricing = [
                {'sku_name': f'{current_sku.split("_")[0]}_Smaller1', 'retail_price': estimated_hourly * 0.5, 'monthly_cost': estimated_hourly * 0.5 * 730},
                {'sku_name': f'{current_sku.split("_")[0]}_Smaller2', 'retail_price': estimated_hourly * 0.75, 'monthly_cost': estimated_hourly * 0.75 * 730},
            ]
            # Calculate estimated hours from fallback pricing
            if current_hourly_rate > 0:
                estimated_hours = billed_cost / current_hourly_rate
            pricing_context = "\n\n" + format_vm_pricing_for_llm(current_pricing, alternative_pricing) + "\n(ESTIMATED - Database unavailable)\n"
    else:
        pricing_context = "\n\nPRICING DATA: Not available (schema or SKU not provided)\n"

    # Extract key metrics for base_of_recommendations (with units)
    metrics_list = []
    for metric_name, values in formatted_metrics.items():
        if values.get('Avg') is not None:
            # Extract unit from metric name if it has one
            if '(GB)' in metric_name or 'Capacity' in metric_name:
                unit = 'GB'
            elif 'CPU' in metric_name or 'Percentage' in metric_name:
                unit = '%'
            elif 'Operations' in metric_name:
                unit = 'ops/sec'
            else:
                unit = ''

            if unit:
                metrics_list.append(f'"{metric_name}: Avg={values["Avg"]:.2f}{unit}, Max={values.get("Max", 0):.2f}{unit}"')
            else:
                metrics_list.append(f'"{metric_name}: Avg={values["Avg"]:.2f}, Max={values.get("Max", 0):.2f}"')

    # Format as JSON array string for LLM
    metrics_list_str = '[' + ', '.join(metrics_list) + ']' if metrics_list else '[]'

    # Build explicit metric summary for the prompt (with units)
    metrics_summary = []
    for metric_name, values in formatted_metrics.items():
        if values.get('Avg') is not None:
            if '(GB)' in metric_name or 'Capacity' in metric_name:
                unit = 'GB'
            elif 'CPU' in metric_name or 'Percentage' in metric_name:
                unit = '%'
            elif 'Operations' in metric_name:
                unit = 'ops/sec'
            else:
                unit = ''

            if unit:
                metrics_summary.append(f"- {metric_name}: Avg={values['Avg']:.2f}{unit}, Max={values.get('Max', 0):.2f}{unit}, MaxDate={values.get('MaxDate', 'N/A')}")
            else:
                metrics_summary.append(f"- {metric_name}: Avg={values['Avg']:.2f}, Max={values.get('Max', 0):.2f}, MaxDate={values.get('MaxDate', 'N/A')}")
    metrics_text = "\n".join(metrics_summary) if metrics_summary else "No metrics available - Cannot provide recommendations"

    # Determine if we can make recommendations
    has_pricing = (current_pricing is not None and alternative_pricing)
    has_metrics = bool(metrics_summary)

    if not has_pricing or not has_metrics or current_sku == "N/A" or current_sku == "None":
        # Cannot make proper recommendations without pricing or metrics
        return f"""Azure VM FinOps. Analyze {resource_id} | SKU: {current_sku} | {start_date} to {end_date} ({duration_days}d) | Cost: ${billed_cost:.2f}

AVAILABLE METRICS:
{metrics_text}

PRICING OPTIONS:
{pricing_context}

ISSUE: {'Missing pricing data' if not has_pricing else 'Missing metrics data' if not has_metrics else 'Unknown SKU'}

OUTPUT (JSON only):
{{
  "recommendations": {{
    "effective_recommendation": {{"text": "Unable to provide recommendations", "explanation": "{'Pricing data unavailable for SKU analysis' if not has_pricing else 'No metrics available for resource analysis' if not has_metrics else 'SKU information not available'}", "saving_pct": 0}},
    "additional_recommendation": [],
    "base_of_recommendations": {metrics_list_str}
  }},
  "cost_forecasting": {{"monthly": {monthly_forecast:.2f}, "annually": {annual_forecast:.2f}}},
  "anomalies": [],
  "contract_deal": {{"assessment": "unknown", "for_sku": "{current_sku}", "reason": "Insufficient data for assessment", "monthly_saving_pct": 0, "annual_saving_pct": 0}}
}}"""

    return f"""Azure VM FinOps. Analyze {resource_id} | SKU: {current_sku} | {start_date} to {end_date} ({duration_days}d)
Current Cost: ${billed_cost:.2f} for {duration_days}d | Forecast: ${monthly_forecast:.2f}/mo, ${annual_forecast:.2f}/yr

AVAILABLE METRICS (MUST USE):
{metrics_text}

PRICING OPTIONS:
{pricing_context}

USAGE CALCULATION:
- Current cost ${billed_cost:.4f} over {duration_days}d
- Current hourly rate: ${current_hourly_rate:.4f}/hr
- Estimated usage: {estimated_hours:.2f} hours
- Monthly forecast: ${monthly_forecast:.2f}

RULES:
1. MUST cite exact metrics above with units (e.g., "CPU: Avg=15.2%, Max=45.8%")
2. MUST use monthly forecast ${monthly_forecast:.2f} for savings calculations
3. To calculate alternative costs: alternative_hourly_rate × {estimated_hours:.2f} hours
   Example: If ALT1 = $0.05/hr, then ALT1 cost = $0.05 × {estimated_hours:.2f} = ${estimated_hours * 0.05 if estimated_hours > 0 else 0:.4f}
4. Each recommendation MUST be unique (downsize vs reserved instance vs schedule vs automation)
5. Calculate: savings_pct = ((monthly_forecast - alternative_cost) / monthly_forecast) * 100
6. Anomalies: MUST use exact MaxDate from metrics, explain why value is unusual
7. Contract assessment: Compare current monthly cost vs alternatives, explain good/bad

OUTPUT (JSON only, NO markdown):
{{
  "recommendations": {{
    "effective_recommendation": {{
      "text": "Primary action with exact SKU name from PRICING",
      "explanation": "Based on [cite exact metrics with units from AVAILABLE METRICS] over {duration_days} days, current monthly forecast is ${monthly_forecast:.2f}. Scaling [Specific SKU from PRICING] hourly rate to same usage pattern = $[scaled cost]/mo, saving $[exact amount].",
      "saving_pct": <number calculated from monthly forecast>
    }},
    "additional_recommendation": [
      {{
        "text": "DIFFERENT recommendation type (e.g., Reserved Instance for current SKU)",
        "explanation": "With [cite metrics], purchasing 1-year RI at [price from PRICING] saves [amount] vs pay-as-you-go.",
        "saving_pct": <number>
      }},
      {{
        "text": "THIRD unique recommendation (e.g., Auto-shutdown during off-hours)",
        "explanation": "Given [metrics showing usage pattern], schedule shutdown 16hrs/day saves ~67% on compute.",
        "saving_pct": <number>
      }}
    ],
    "base_of_recommendations": {metrics_list_str}
  }},
  "cost_forecasting": {{"monthly": {monthly_forecast:.2f}, "annually": {annual_forecast:.2f}}},
  "anomalies": [
    {{"metric_name": "Exact name from AVAILABLE METRICS", "timestamp": "Exact MaxDate from metrics", "value": <exact Max value from metrics>, "reason_short": "Explain why this max value is unusual (spike/drop/sustained high)"}},
    {{"metric_name": "Another metric name", "timestamp": "Its MaxDate", "value": <its Max value>, "reason_short": "Why unusual for this resource type"}}
  ],
  "contract_deal": {{
    "assessment": "good|bad|unknown",
    "for_sku": "{current_sku}",
    "reason": "Current monthly forecast ${monthly_forecast:.2f} vs [specific alternative from PRICING] at [price]/mo = [comparison]. {'Good deal if current is cheaper' if billed_cost < monthly_forecast else 'Bad deal if overpaying'}",
    "monthly_saving_pct": <number>,
    "annual_saving_pct": <number>
  }}
}}"""

# --- EXPORTED LLM CALL FUNCTIONS (with logging) ---

def get_storage_recommendation_single(resource_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Generates cost recommendations for a single Azure Storage Account.
    """
    if not resource_data:
        logging.warning("Received empty resource_data for storage.")
        return None

    billed_cost = resource_data.get("billed_cost", 0.0)
    duration_days = int(resource_data.get("duration_days", 30) or 30)
    start_date = resource_data.get("start_date", "N/A")
    end_date = resource_data.get("end_date", "N/A")
    resource_id = resource_data.get('resource_id', 'Unknown')
    
    forecast = _extrapolate_costs(billed_cost, duration_days)
    prompt = _generate_storage_prompt(resource_data, start_date, end_date, forecast['monthly'], forecast['annually'])
    
    raw = llm_call(prompt)
    if not raw:
        logging.error(f"Empty LLM response for storage resource {resource_id}")
        return None

    # NOTE: Assuming extract_json_str is available and correctly imported
    json_str = extract_json_str(raw)
    if not json_str:
        logging.error(f"Could not extract JSON from LLM output for storage resource {resource_id}. Raw output:\n{raw[:200]}...")
        return None

    try:
        parsed = json.loads(json_str)
        if not isinstance(parsed, dict):
            logging.error(f"LLM storage response parsed to non-dict: {type(parsed)} for {resource_id}")
            return None
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON (after extraction) for storage resource {resource_id}. Extracted string:\n{json_str[:200]}...")
        return None

    parsed['resource_id'] = resource_id
    parsed['_forecast_monthly'] = forecast['monthly']
    parsed['_forecast_annual'] = forecast['annually']
    return parsed


def get_compute_recommendation_single(resource_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Generates cost recommendations for a single VM resource.
    """
    if not resource_data:
        logging.warning("Received empty resource_data for compute.")
        return None

    billed_cost = resource_data.get("billed_cost", 0.0)
    duration_days = int(resource_data.get("duration_days", 30) or 30)
    start_date = resource_data.get("start_date", "N/A")
    end_date = resource_data.get("end_date", "N/A")
    resource_id = resource_data.get('resource_id', 'Unknown')

    forecast = _extrapolate_costs(billed_cost, duration_days)
    prompt = _generate_compute_prompt(resource_data, start_date, end_date, forecast['monthly'], forecast['annually'])
    
    raw = llm_call(prompt)
    if not raw:
        logging.error(f"Empty LLM response for compute resource {resource_id}")
        return None

    # NOTE: Assuming extract_json_str is available and correctly imported
    json_str = extract_json_str(raw)
    if not json_str:
        logging.error(f"Could not extract JSON from LLM output for compute resource {resource_id}. Raw output:\n{raw[:200]}...")
        return None

    try:
        parsed = json.loads(json_str)
        if not isinstance(parsed, dict):
            logging.error(f"LLM compute response parsed to non-dict: {type(parsed)} for {resource_id}")
            return None
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON (after extraction) for compute resource {resource_id}. Extracted string:\n{json_str[:200]}...")
        return None

    parsed['resource_id'] = resource_id
    parsed['_forecast_monthly'] = forecast['monthly']
    parsed['_forecast_annual'] = forecast['annually']
    return parsed


# Backwards-compatible wrappers (process lists but only the first element)
def get_storage_recommendation(data: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """Wrapper for backward compatibility, processes only the first resource."""
    if not data:
        return None
    # Only process first resource (single-resource flow)
    single = get_storage_recommendation_single(data[0])
    return [single] if single else None

def get_compute_recommendation(data: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """Wrapper for backward compatibility, processes only the first resource."""
    if not data:
        return None
    single = get_compute_recommendation_single(data[0])
    return [single] if single else None

def _generate_public_ip_prompt(resource_data: dict, start_date: str, end_date: str, monthly_forecast: float, annual_forecast: float) -> str:
    """Generates the structured prompt for Public IP LLM analysis with dynamically included metrics and pricing."""

    # Prepare the structured metrics for the prompt (only PublicIP-relevant metrics)
    formatted_metrics = _format_metrics_for_llm(resource_data, resource_type="publicip")
    current_sku = resource_data.get("sku", "N/A")
    current_tier = resource_data.get("tier", "N/A")
    ip_address = resource_data.get("ip_address", "N/A")
    allocation_method = resource_data.get("allocation_method", "N/A")
    billed_cost = resource_data.get("billed_cost", 0.0)

    # Fetch pricing data from database
    schema_name = resource_data.get("schema_name", "")
    region = resource_data.get("region", "eastus")

    pricing_context = ""
    if schema_name:
        try:
            # Get public IP pricing context
            ip_pricing = get_public_ip_pricing_context(schema_name, region)

            # Debug: Print fetched pricing
            print(f"\n{'='*60}")
            print(f"PRICING DEBUG - Azure Public IP: {current_sku} {current_tier} in {region}")
            print(f"{'='*60}")
            print(f"PUBLIC IP PRICING OPTIONS:")
            options = ip_pricing.get('options', [])
            if options:
                for idx, opt in enumerate(options, 1):
                    print(f"  {idx}. {opt['meter_name']}: {opt['retail_price']:.6f}/hr ({opt['monthly_cost']:.2f}/month)")
            else:
                print(f"  No pricing options available")
            print(f"{'='*60}\n")

            # Format pricing for LLM
            pricing_context = "\n\n" + format_ip_pricing_for_llm(ip_pricing) + "\n"
        except Exception as e:
            print(f"⚠️ Error fetching Public IP pricing data: {e}")
            pricing_context = "\n\nPRICING DATA: Not available\n"
    else:
        pricing_context = "\n\nPRICING DATA: Not available (schema not provided)\n"

    # Check if we have pricing and metrics available
    has_pricing = pricing_context and "Not available" not in pricing_context
    has_metrics = bool(formatted_metrics)

    # Extract key metrics for base_of_recommendations (with units)
    metrics_list = []
    for metric_name, values in formatted_metrics.items():
        if values.get('Avg') is not None:
            # Extract unit from metric name if it has one
            if 'Bytes' in metric_name or 'Throughput' in metric_name:
                unit = 'Bps'
            elif 'Packets' in metric_name:
                unit = 'pps'
            elif 'DDoS' in metric_name or 'Attack' in metric_name:
                unit = 'count'
            elif 'Percentage' in metric_name:
                unit = '%'
            else:
                unit = ''

            if unit:
                metrics_list.append(f'"{metric_name}: Avg={values["Avg"]:.2f}{unit}, Max={values.get("Max", 0):.2f}{unit}"')
            else:
                metrics_list.append(f'"{metric_name}: Avg={values["Avg"]:.2f}, Max={values.get("Max", 0):.2f}"')

    # Format as JSON array string for LLM
    metrics_list_str = '[' + ', '.join(metrics_list) + ']' if metrics_list else '[]'

    # Build explicit metric summary for the prompt (with units)
    metrics_summary = []
    for metric_name, values in formatted_metrics.items():
        if values.get('Avg') is not None:
            if 'Bytes' in metric_name or 'Throughput' in metric_name:
                unit = 'Bps'
            elif 'Packets' in metric_name:
                unit = 'pps'
            elif 'DDoS' in metric_name or 'Attack' in metric_name:
                unit = 'count'
            elif 'Percentage' in metric_name:
                unit = '%'
            else:
                unit = ''

            if unit:
                metrics_summary.append(f"- {metric_name}: Avg={values['Avg']:.2f}{unit}, Max={values.get('Max', 0):.2f}{unit}, MaxDate={values.get('MaxDate', 'N/A')}")
            else:
                metrics_summary.append(f"- {metric_name}: Avg={values['Avg']:.2f}, Max={values.get('Max', 0):.2f}, MaxDate={values.get('MaxDate', 'N/A')}")

    metrics_text = "\n".join(metrics_summary) if metrics_summary else "No metrics available"
    duration_days = resource_data.get("duration_days", 30)

    # Guard clause for missing data
    if not has_pricing or not has_metrics or current_sku == "N/A" or current_sku == "None":
        return f"""Azure Public IP FinOps. Analyze {resource_data.get("resource_id", "N/A")} | SKU: {current_sku} {current_tier} | IP: {ip_address} ({allocation_method}) | {start_date} to {end_date} ({duration_days}d) | Cost: ${billed_cost:.2f}

AVAILABLE METRICS:
{metrics_text}

PRICING OPTIONS:
{pricing_context}

ISSUE: {'Missing pricing data' if not has_pricing else 'Missing metrics data' if not has_metrics else 'Unknown SKU'}

OUTPUT (JSON only):
{{
  "recommendations": {{
    "effective_recommendation": {{"text": "Unable to provide recommendations", "explanation": "{'Pricing data unavailable for allocation analysis' if not has_pricing else 'No metrics available for resource analysis' if not has_metrics else 'SKU information not available'}", "saving_pct": 0}},
    "additional_recommendation": [],
    "base_of_recommendations": {metrics_list_str}
  }},
  "cost_forecasting": {{"monthly": {monthly_forecast:.2f}, "annually": {annual_forecast:.2f}}},
  "anomalies": [],
  "contract_deal": {{"assessment": "unknown", "for_sku": "{current_sku}", "reason": "Insufficient data for assessment", "monthly_saving_pct": 0, "annual_saving_pct": 0}}
}}"""

    return f"""Azure Public IP FinOps. Analyze {resource_data.get("resource_id", "N/A")} | {current_sku} {current_tier} | IP: {ip_address} ({allocation_method}) | {start_date} to {end_date} ({duration_days}d)
Current Cost: ${billed_cost:.2f} for {duration_days}d | Forecast: ${monthly_forecast:.2f}/mo, ${annual_forecast:.2f}/yr

AVAILABLE METRICS (MUST USE):
{metrics_text}

PRICING OPTIONS:
{pricing_context}

RULES:
1. MUST cite exact metrics above with units (e.g., "BytesInDDoS: Avg=150.2Bps, Max=500.5Bps")
2. MUST use monthly forecast ${monthly_forecast:.2f} for savings calculations
3. PRICING shows 24/7 costs for Public IPs. Current forecast ${monthly_forecast:.2f} is based on actual usage over {duration_days}d.
4. Each recommendation MUST be unique (deallocate unused vs change allocation method vs reserved IP vs DDoS protection)
5. Calculate: savings_pct = ((current_monthly_forecast - alternative_cost) / current_monthly_forecast) * 100
6. Anomalies: MUST use exact MaxDate from metrics, explain why value is unusual
7. Contract assessment: Compare current monthly cost vs alternatives, explain good/bad

OUTPUT (JSON only, NO markdown):
{{
  "recommendations": {{
    "effective_recommendation": {{
      "text": "Primary action with exact allocation method from PRICING",
      "explanation": "Based on [cite exact metrics with units from AVAILABLE METRICS] over {duration_days} days, current monthly forecast is ${monthly_forecast:.2f}. [Specific allocation from PRICING] costs [exact price]/mo, saving [exact amount].",
      "saving_pct": <number calculated from monthly forecast>
    }},
    "additional_recommendation": [
      {{
        "text": "DIFFERENT recommendation type (e.g., Deallocate IP when not in use)",
        "explanation": "With [cite usage metrics], IP shows [low usage pattern]. Deallocating when idle saves [amount]/mo vs static allocation.",
        "saving_pct": <number>
      }},
      {{
        "text": "THIRD unique recommendation (e.g., Use Azure Load Balancer frontend IP instead)",
        "explanation": "Given [traffic pattern from metrics], consolidating behind load balancer eliminates need for dedicated public IP.",
        "saving_pct": <number>
      }}
    ],
    "base_of_recommendations": {metrics_list_str}
  }},
  "cost_forecasting": {{"monthly": {monthly_forecast:.2f}, "annually": {annual_forecast:.2f}}},
  "anomalies": [
    {{"metric_name": "Exact name from AVAILABLE METRICS", "timestamp": "Exact MaxDate from metrics", "value": <exact Max value from metrics>, "reason_short": "Explain why this max value is unusual (spike/drop/attack)"}},
    {{"metric_name": "Another metric name", "timestamp": "Its MaxDate", "value": <its Max value>, "reason_short": "Why unusual for this resource type"}}
  ],
  "contract_deal": {{
    "assessment": "good|bad|unknown",
    "for_sku": "{current_sku}",
    "reason": "Current monthly forecast ${monthly_forecast:.2f} vs [specific alternative from PRICING] at [price]/mo = [comparison]. {'Good deal if current is cheaper' if billed_cost < monthly_forecast else 'Bad deal if overpaying'}",
    "monthly_saving_pct": <number>,
    "annual_saving_pct": <number>
  }}
}}"""


def get_public_ip_recommendation_single(resource_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Generates cost recommendations for a single Azure Public IP resource.
    """
    if not resource_data:
        logging.warning("Received empty resource_data for Public IP.")
        return None

    billed_cost = resource_data.get("billed_cost", 0.0)
    duration_days = int(resource_data.get("duration_days", 30) or 30)
    start_date = resource_data.get("start_date", "N/A")
    end_date = resource_data.get("end_date", "N/A")
    resource_id = resource_data.get('resource_id', 'Unknown')

    # Check if metrics data exists
    has_metrics = any(k.startswith("metric_") for k in resource_data.keys())
    if not has_metrics:
        logging.warning(f"Public IP {resource_id} has no metrics data - analysis may be limited")

    forecast = _extrapolate_costs(billed_cost, duration_days)
    prompt = _generate_public_ip_prompt(resource_data, start_date, end_date, forecast['monthly'], forecast['annually'])
    
    raw = llm_call(prompt)
    if not raw:
        logging.error(f"Empty LLM response for Public IP resource {resource_id}")
        return None

    # NOTE: Assuming extract_json_str is available and correctly imported
    json_str = extract_json_str(raw)
    if not json_str:
        logging.error(f"Could not extract JSON from LLM output for Public IP resource {resource_id}. Raw output:\n{raw[:200]}...")
        return None

    try:
        parsed = json.loads(json_str)
        if not isinstance(parsed, dict):
            logging.error(f"LLM Public IP response parsed to non-dict: {type(parsed)} for {resource_id}")
            return None
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON (after extraction) for Public IP resource {resource_id}. Extracted string:\n{json_str[:200]}...")
        return None

    parsed['resource_id'] = resource_id
    parsed['_forecast_monthly'] = forecast['monthly']
    parsed['_forecast_annual'] = forecast['annually']
    return parsed


def get_public_ip_recommendation(data: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """Wrapper for backward compatibility, processes only the first resource."""
    if not data:
        return None
    # Only process first resource (single-resource flow)
    single = get_public_ip_recommendation_single(data[0])
    return [single] if single else None
