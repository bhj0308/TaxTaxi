"""
HTS Quantitative Details Search (USITC HTS)

Goal:
- Given a full HTS code, retrieve every quantitative detail related to that code
- Includes tariff rates, units of measure, special rates, and all numerical data

Data source:
- USITC HTS REST endpoint:
  - ExportList: https://hts.usitc.gov/reststop/exportList?format=JSON&from=...&to=...&styles=false

This module uses the API functions from usitc_mapping.py for consistency.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Import API functions from usitc_mapping
from usitc_mapping import (
    fetch_exportlist,
    normalize_hts_input,
    APIError,
    _clean_text,
)


@dataclass
class QuantitativeDetail:
    """Represents a quantitative detail field from the HTS data."""
    field_name: str
    value: Any
    data_type: str  # "number", "string", "boolean", "null"


@dataclass
class HTSQuantitativeResult:
    """Result containing all quantitative details for an HTS code."""
    hts_code: str
    requested_line: Optional[Dict[str, Any]] = None  # Exact target row
    effective_duty_line: Optional[Dict[str, Any]] = None  # Row with duty rates
    effective_hts_code: Optional[str] = None
    resolved_view: Dict[str, Any] = field(default_factory=dict)  # Merged view
    quantitative_fields: List[QuantitativeDetail] = field(default_factory=list)
    hierarchical_context: List[Dict[str, Any]] = field(default_factory=list)
    fallback_chain: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary for JSON serialization."""
        result_dict = {
            "hts_code": self.hts_code,
            "requested_line": self.requested_line,
            "effective_duty_line": self.effective_duty_line,
            "effective_hts_code": self.effective_hts_code,
            "resolved_view": self.resolved_view,
            "quantitative_fields": [
                {
                    "field_name": qf.field_name,
                    "value": qf.value,
                    "data_type": qf.data_type
                }
                for qf in self.quantitative_fields
            ],
            "hierarchical_context": self.hierarchical_context,
            "fallback_chain": self.fallback_chain,
            "error_message": self.error_message
        }
        return result_dict
    
    def to_compact_dict(self) -> Dict[str, Any]:
        """
        Convert result to compact dictionary with only useful information.
        Saves: hts_code, effective_hts_code, description, duty rates, units, footnotes, quota, fallback_chain.
        """
        compact = {
            "hts_code": self.hts_code,
            "effective_hts_code": self.effective_hts_code,
            "fallback_chain": self.fallback_chain,
        }
        
        # Extract description from requested_line
        if self.requested_line:
            description = _clean_text(self.requested_line.get("description", ""))
            if description:
                compact["description"] = description
        
        # Extract useful fields from resolved_view
        if self.resolved_view:
            # Duty rates
            for field in ("general", "special", "other"):
                value = self.resolved_view.get(field)
                if value and str(value).strip():
                    compact[field] = value
            
            # Units
            units = self.resolved_view.get("units")
            if units:
                compact["units"] = units
            
            # Footnotes
            footnotes = self.resolved_view.get("footnotes")
            if footnotes:
                compact["footnotes"] = footnotes
            
            # Quota
            quota = self.resolved_view.get("quotaQuantity")
            if quota is not None:
                compact["quotaQuantity"] = quota
            
            # Additional duties
            additional_duties = self.resolved_view.get("additionalDuties")
            if additional_duties is not None:
                compact["additionalDuties"] = additional_duties
        
        return compact
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HTSQuantitativeResult":
        """Create result from dictionary."""
        quantitative_fields = [
            QuantitativeDetail(
                field_name=qf["field_name"],
                value=qf["value"],
                data_type=qf["data_type"]
            )
            for qf in data.get("quantitative_fields", [])
        ]
        
        return cls(
            hts_code=data["hts_code"],
            requested_line=data.get("requested_line"),
            effective_duty_line=data.get("effective_duty_line"),
            effective_hts_code=data.get("effective_hts_code"),
            resolved_view=data.get("resolved_view", {}),
            quantitative_fields=quantitative_fields,
            hierarchical_context=data.get("hierarchical_context", []),
            fallback_chain=data.get("fallback_chain", []),
            error_message=data.get("error_message")
        )


def is_numeric_value(value: Any) -> bool:
    """Check if a value is numeric (int, float, or numeric string)."""
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        # Try to parse as number
        try:
            float(value.replace(",", "").replace("%", "").strip())
            return True
        except (ValueError, AttributeError):
            return False
    return False


def format_hts_code(digits: str) -> str:
    """Format digit string as HTS code with dots."""
    if len(digits) >= 10:
        return f"{digits[:4]}.{digits[4:6]}.{digits[6:8]}.{digits[8:10]}"
    elif len(digits) >= 8:
        return f"{digits[:4]}.{digits[4:6]}.{digits[6:8]}"
    elif len(digits) >= 6:
        return f"{digits[:4]}.{digits[4:6]}"
    elif len(digits) >= 4:
        return digits[:4]
    return digits


def get_ancestor_codes(hts_code: str) -> List[str]:
    """
    Get all ancestor codes for an HTS code (8-digit, 6-digit, 4-digit).
    Returns list from deepest to shallowest (excluding the code itself).
    """
    code_digits = hts_code.replace(".", "")
    ancestors = []
    
    if len(code_digits) >= 10:
        ancestors.append(format_hts_code(code_digits[:8]))  # 8-digit
    if len(code_digits) >= 8:
        ancestors.append(format_hts_code(code_digits[:6]))  # 6-digit
    if len(code_digits) >= 6:
        ancestors.append(format_hts_code(code_digits[:4]))  # 4-digit
    
    return ancestors


def extract_quantitative_fields(data: Dict[str, Any]) -> List[QuantitativeDetail]:
    """
    Extract quantitative fields from HTS data.
    Focuses on: duty rates, units, quotas, quantities, rates, duties.
    Also includes nested structures like units (list) and footnotes (list).
    """
    quantitative = []
    
    # Always include these fields if present (even if None, we track them)
    priority_fields = [
        "general", "special", "other",  # Duty rates
        "quotaQuantity",  # Quotas
        "units",  # Units of measure (list)
        "footnotes",  # Footnotes (list, may contain chapter 99 refs)
    ]
    
    # Fields that contain quantitative info (rate, quantity, duties, etc.)
    quantitative_keywords = ["rate", "quantity", "duties", "duty", "quota", "unit"]
    
    for key, value in data.items():
        if value is None:
            # Skip None for now, but we track priority fields
            if key in priority_fields:
                quantitative.append(QuantitativeDetail(
                    field_name=key,
                    value=None,
                    data_type="null"
                ))
            continue
        
        # Priority fields: always include
        if key in priority_fields:
            if isinstance(value, list):
                quantitative.append(QuantitativeDetail(
                    field_name=key,
                    value=value,
                    data_type="list"
                ))
            elif isinstance(value, (int, float)):
                quantitative.append(QuantitativeDetail(
                    field_name=key,
                    value=value,
                    data_type="number"
                ))
            elif isinstance(value, str) and value.strip():
                # Check if it's numeric
                if is_numeric_value(value):
                    cleaned = value.replace(",", "").replace("%", "").strip()
                    try:
                        numeric_value = float(cleaned) if "." in cleaned else int(cleaned)
                        quantitative.append(QuantitativeDetail(
                            field_name=key,
                            value=numeric_value,
                            data_type="number"
                        ))
                    except ValueError:
                        quantitative.append(QuantitativeDetail(
                            field_name=key,
                            value=value,
                            data_type="string"
                        ))
                else:
                    quantitative.append(QuantitativeDetail(
                        field_name=key,
                        value=value,
                        data_type="string"
                    ))
            elif isinstance(value, bool):
                quantitative.append(QuantitativeDetail(
                    field_name=key,
                    value=value,
                    data_type="boolean"
                ))
        
        # Other fields with quantitative keywords
        elif any(kw in key.lower() for kw in quantitative_keywords):
            if is_numeric_value(value):
                if isinstance(value, str):
                    cleaned = value.replace(",", "").replace("%", "").strip()
                    try:
                        numeric_value = float(cleaned) if "." in cleaned else int(cleaned)
                    except ValueError:
                        numeric_value = value
                else:
                    numeric_value = value
                quantitative.append(QuantitativeDetail(
                    field_name=key,
                    value=numeric_value,
                    data_type="number"
                ))
            elif isinstance(value, (list, dict)):
                quantitative.append(QuantitativeDetail(
                    field_name=key,
                    value=value,
                    data_type=type(value).__name__
                ))
    
    return quantitative


def resolve_effective_duty_line(
    requested_line: dict | None,
    ancestor_lines: list[dict],
    target_hts: str
) -> tuple[dict | None, list[str]]:
    """
    Given the requested line and ancestor lines, return the row that
    legally defines the duty rate, along with the fallback chain.
    
    Args:
        requested_line: The exact target row (may be None if not found)
        ancestor_lines: List of ancestor rows (8-digit, 6-digit, 4-digit)
        target_hts: Target HTS code to resolve
    
    Returns:
        Tuple of (effective_duty_line_dict, fallback_chain_list)
        - effective_duty_line_dict: The row with duty rate info, or None if not found
        - fallback_chain_list: List of HTS codes checked (deepest to shallowest)
    """
    # Build candidate list: requested first, then ancestors (deepest to shallowest)
    candidates = []
    fallback_chain = []
    
    # Add requested line first if it exists
    if requested_line:
        code = _clean_text(requested_line.get("htsno", ""))
        if code:
            candidates.append(requested_line)
            fallback_chain.append(code)
    
    # Add ancestors (already sorted deepest to shallowest from get_ancestor_codes)
    for item in ancestor_lines:
        code = _clean_text(item.get("htsno", ""))
        if code and code not in fallback_chain:
            candidates.append(item)
            fallback_chain.append(code)
    
    # Return first row with any duty rate
    for item in candidates:
        if any(
            (item.get(field) or "").strip()
            for field in ("general", "special", "other")
        ):
            return item, fallback_chain
    
    return None, fallback_chain


def fetch_hts_row(hts_code: str) -> dict | None:
    """
    Fetch a single HTS row by querying from=to=hts_code.
    Returns the row if found, None otherwise.
    """
    try:
        data = fetch_exportlist(hts_code, hts_code)
        if not data:
            return None
        
        # Find exact match
        for item in data:
            code = _clean_text(item.get("htsno", ""))
            if code == hts_code:
                return item
        
        # If no exact match, return first item with a code (might be hierarchical context)
        for item in data:
            code = _clean_text(item.get("htsno", ""))
            if code:
                return item
        
        return data[0] if data else None
    except APIError:
        return None


def merge_resolved_view(
    requested_line: dict | None,
    effective_duty_line: dict | None
) -> dict:
    """
    Merge requested line and effective duty line into a resolved view.
    - Units, quotas, and suffix-specific fields come from requested_line
    - Duty rates (general, special, other) come from effective_duty_line
    """
    resolved = {}
    
    # Start with requested line (has units, quotas, suffix details)
    if requested_line:
        resolved.update(requested_line)
    
    # Override/add duty rate fields from effective duty line
    if effective_duty_line:
        for field in ("general", "special", "other"):
            value = effective_duty_line.get(field)
            if value and str(value).strip():
                resolved[field] = value
    
    return resolved


def search_hts_quantitative_details(hts_code: str) -> HTSQuantitativeResult:
    """
    Get every quantitative detail related to a full HTS code.
    
    Strategy:
    1. Fetch exact target row (from=to=target)
    2. Compute and fetch each ancestor separately (8-digit, 6-digit, 4-digit)
    3. Resolve effective duty line from the fetched set
    4. Merge requested line (units/quota) with effective duty line (rates)
    
    Args:
        hts_code: Full HTS code (e.g., "0901.21.00", "6109.10.00.00")
    
    Returns:
        HTSQuantitativeResult with all quantitative details and full data
    """
    normalized_code = normalize_hts_input(hts_code)
    
    if not normalized_code:
        return HTSQuantitativeResult(
            hts_code=hts_code,
            error_message="Invalid HTS code: empty or invalid format"
        )
    
    try:
        # Step 1: Fetch exact target row
        requested_line = fetch_hts_row(normalized_code)
        
        if not requested_line:
            return HTSQuantitativeResult(
                hts_code=normalized_code,
                error_message=f"Exact HTS code not found: {normalized_code}"
            )
        
        # Verify it's the exact code we requested
        requested_code = _clean_text(requested_line.get("htsno", ""))
        if requested_code != normalized_code:
            return HTSQuantitativeResult(
                hts_code=normalized_code,
                error_message=f"Exact HTS code not found: {normalized_code} (got {requested_code} instead)"
            )
        
        # Step 2: Compute and fetch ancestor codes separately
        ancestor_codes = get_ancestor_codes(normalized_code)
        ancestor_lines = []
        all_context = [requested_line]  # Start with requested line
        
        for ancestor_code in ancestor_codes:
            ancestor_row = fetch_hts_row(ancestor_code)
            if ancestor_row:
                ancestor_lines.append(ancestor_row)
                all_context.append(ancestor_row)
        
        # Step 3: Resolve effective duty line
        effective_duty_line, fallback_chain = resolve_effective_duty_line(
            requested_line, ancestor_lines, normalized_code
        )
        
        effective_hts_code = None
        if effective_duty_line:
            effective_hts_code = _clean_text(effective_duty_line.get("htsno", ""))
        
        # Step 4: Create merged resolved view
        resolved_view = merge_resolved_view(requested_line, effective_duty_line)
        
        # Extract quantitative fields from resolved view
        quantitative_fields = extract_quantitative_fields(resolved_view)
        
        return HTSQuantitativeResult(
            hts_code=normalized_code,
            requested_line=requested_line,
            effective_duty_line=effective_duty_line,
            effective_hts_code=effective_hts_code,
            resolved_view=resolved_view,
            quantitative_fields=quantitative_fields,
            hierarchical_context=all_context,
            fallback_chain=fallback_chain
        )
        
    except APIError as e:
        return HTSQuantitativeResult(
            hts_code=normalized_code,
            error_message=f"API error: {e.message} (retries: {e.retries_attempted})"
        )
    except Exception as e:
        return HTSQuantitativeResult(
            hts_code=normalized_code,
            error_message=f"Unexpected error: {str(e)}"
        )


# JSON storage configuration
RESULTS_JSON_FILE = "hts_search_results.json"


def load_stored_results() -> Dict[str, Dict[str, Any]]:
    """
    Load stored search results from JSON file.
    Returns a dictionary mapping HTS codes to their result data.
    """
    if not os.path.exists(RESULTS_JSON_FILE):
        return {}
    
    try:
        with open(RESULTS_JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # Ensure it's a dict mapping HTS codes to results
            if isinstance(data, dict):
                return data
            # If it's a list, convert to dict
            elif isinstance(data, list):
                return {item.get("hts_code", ""): item for item in data if item.get("hts_code")}
            return {}
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Could not load stored results: {e}", file=sys.stderr)
        return {}


def save_result_to_json(result: HTSQuantitativeResult) -> bool:
    """
    Save search result to JSON file if it doesn't already exist.
    Returns True if saved (new), False if already exists.
    """
    if result.error_message:
        # Don't save error results
        return False
    
    # Load existing results
    stored_results = load_stored_results()
    
    # Check if this HTS code already exists
    if result.hts_code in stored_results:
        return False  # Already exists
    
    # Add new result (save only useful information)
    stored_results[result.hts_code] = result.to_compact_dict()
    
    # Save to file
    try:
        with open(RESULTS_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(stored_results, f, indent=2, ensure_ascii=False)
        return True  # Successfully saved
    except IOError as e:
        print(f"Warning: Could not save result to JSON: {e}", file=sys.stderr)
        return False


def get_stored_result(hts_code: str) -> Optional[HTSQuantitativeResult]:
    """
    Get a stored result for an HTS code if it exists.
    Returns HTSQuantitativeResult or None if not found.
    Note: Stored results are in compact format, so we reconstruct a minimal result.
    """
    stored_results = load_stored_results()
    if hts_code in stored_results:
        try:
            stored_data = stored_results[hts_code]
            
            # Reconstruct from compact format
            # Build resolved_view from stored fields
            resolved_view = {}
            for field in ("general", "special", "other", "units", "footnotes", "quotaQuantity", "additionalDuties"):
                if field in stored_data:
                    resolved_view[field] = stored_data[field]
            
            # Build requested_line if description exists
            requested_line = None
            if "description" in stored_data:
                requested_line = {"description": stored_data["description"], "htsno": stored_data["hts_code"]}
            
            # Build effective_duty_line if duty rates exist
            effective_duty_line = None
            if any(field in stored_data for field in ("general", "special", "other")):
                effective_duty_line = {}
                for field in ("general", "special", "other"):
                    if field in stored_data:
                        effective_duty_line[field] = stored_data[field]
                if "effective_hts_code" in stored_data:
                    effective_duty_line["htsno"] = stored_data["effective_hts_code"]
            
            # Extract quantitative fields from resolved_view
            quantitative_fields = extract_quantitative_fields(resolved_view)
            
            return HTSQuantitativeResult(
                hts_code=stored_data["hts_code"],
                requested_line=requested_line,
                effective_duty_line=effective_duty_line,
                effective_hts_code=stored_data.get("effective_hts_code"),
                resolved_view=resolved_view,
                quantitative_fields=quantitative_fields,
                hierarchical_context=[],  # Not stored in compact format
                fallback_chain=stored_data.get("fallback_chain", []),
                error_message=None
            )
        except Exception as e:
            print(f"Warning: Could not parse stored result: {e}", file=sys.stderr)
            return None
    return None


def print_quantitative_result(result: HTSQuantitativeResult) -> None:
    """Pretty print the quantitative details result."""
    print("=" * 80)
    print(f"HTS Code: {result.hts_code}")
    print("=" * 80)
    
    if result.error_message:
        print(f"\nERROR: {result.error_message}\n")
        return
    
    # Show description from requested line
    if result.requested_line:
        description = _clean_text(result.requested_line.get("description", ""))
        if description:
            print(f"\nDescription: {description}\n")
    
    # Show effective duty line and fallback chain
    if result.effective_duty_line or result.fallback_chain:
        print("-" * 80)
        print("EFFECTIVE DUTY LINE:")
        print("-" * 80)
        if result.effective_hts_code and result.effective_hts_code != result.hts_code:
            print(f"  Requested HTS Code: {result.hts_code}")
            print(f"  Effective HTS Code: {result.effective_hts_code} (fallback)")
        else:
            print(f"  Effective HTS Code: {result.effective_hts_code or result.hts_code}")
        
        # Always show fallback chain if available
        if result.fallback_chain:
            print(f"\n  Fallback Chain (checked in order, deepest to shallowest):")
            for i, code in enumerate(result.fallback_chain, 1):
                marker = " ← USED" if code == (result.effective_hts_code or result.hts_code) else ""
                print(f"    {i}. {code}{marker}")
        
        # Show duty rate fields from effective duty line
        if result.effective_duty_line:
            duty_fields = {}
            for field in ("general", "special", "other"):
                value = result.effective_duty_line.get(field, "")
                if value and str(value).strip():
                    duty_fields[field] = value
            
            if duty_fields:
                print(f"\n  Duty Rates (from effective duty line):")
                for field, value in duty_fields.items():
                    print(f"    {field.capitalize():10} = {value}")
            else:
                print(f"\n  No duty rate information found in effective duty line.")
        else:
            print(f"\n  No effective duty line found (no duty rates in any ancestor codes).")
        print()
    
    # Show resolved view (merged: units from requested + rates from effective)
    if result.resolved_view:
        print("-" * 80)
        print("RESOLVED VIEW (Merged: Units/Quota from Requested + Rates from Effective):")
        print("-" * 80)
        
        # Show key fields from resolved view
        key_fields = ["units", "quotaQuantity", "general", "special", "other", "footnotes"]
        for field in key_fields:
            value = result.resolved_view.get(field)
            if value is not None:
                if isinstance(value, list):
                    value_str = str(value)
                elif isinstance(value, str) and value.strip():
                    value_str = value
                elif value:
                    value_str = str(value)
                else:
                    continue
                
                if len(value_str) > 60:
                    value_str = value_str[:57] + "..."
                print(f"  {field:30} = {value_str}")
        print()
    
    print("-" * 80)
    print("QUANTITATIVE DETAILS (from Resolved View):")
    print("-" * 80)
    
    if not result.quantitative_fields:
        print("  No quantitative fields found.")
    else:
        for detail in result.quantitative_fields:
            value_str = str(detail.value)
            if detail.data_type == "number" and isinstance(detail.value, (int, float)):
                # Format numbers nicely
                if isinstance(detail.value, float):
                    value_str = f"{detail.value:.6f}".rstrip("0").rstrip(".")
                else:
                    value_str = f"{detail.value:,}"
            elif detail.data_type in ("list", "dict"):
                value_str = str(detail.value)[:60] + "..." if len(str(detail.value)) > 60 else str(detail.value)
            
            print(f"  {detail.field_name:30} = {value_str:40} ({detail.data_type})")
    
    print("\n" + "-" * 80)
    print("RESOLVED VIEW (All Fields):")
    print("-" * 80)
    
    for key, value in sorted(result.resolved_view.items()):
        if value is None:
            value_str = "null"
        elif isinstance(value, str):
            value_str = value[:60] + "..." if len(value) > 60 else value
        elif isinstance(value, (list, dict)):
            value_str = str(value)[:60] + "..." if len(str(value)) > 60 else str(value)
        else:
            value_str = str(value)
        print(f"  {key:30} = {value_str}")
    
    if result.hierarchical_context:
        print("\n" + "-" * 80)
        print(f"HIERARCHICAL CONTEXT ({len(result.hierarchical_context)} entries):")
        print("-" * 80)
        for i, item in enumerate(result.hierarchical_context[:20]):  # Show first 20
            code = _clean_text(item.get("htsno", ""))
            desc = _clean_text(item.get("description", ""))
            # Ensure indent is an integer
            indent_val = item.get("indent", 0)
            try:
                indent = int(indent_val) if indent_val is not None else 0
            except (ValueError, TypeError):
                indent = 0
            
            if code:
                print(f"  {'  ' * indent}{code:20} {desc[:50]}")
            elif desc:
                print(f"  {'  ' * indent}{'':20} {desc[:50]}")
        
        if len(result.hierarchical_context) > 20:
            print(f"\n  ... ({len(result.hierarchical_context) - 20} more entries)")
    
    print("=" * 80)


# ----------------------------
# CLI usage
# ----------------------------

def main(argv: List[str]) -> int:
    """Command-line interface for HTS quantitative details search."""
    if len(argv) < 2:
        print("HTS Quantitative Details Search")
        print("=" * 50)
        print("\nUsage:")
        print("  python usitc_search.py <hts_code>")
        print("\nExamples:")
        print("  python usitc_search.py 0901.21.00")
        print("  python usitc_search.py 6109.10.00.00")
        print("  python usitc_search.py 0901.21.00.00")
        print("\nNote: Provide a full HTS code to get all quantitative details.")
        return 1
    
    hts_code = " ".join(argv[1:]).strip()
    normalized_code = normalize_hts_input(hts_code)
    
    print(f"\nSearching quantitative details for HTS code: {hts_code}\n")
    
    # Check if result already exists in stored results (use normalized code)
    stored_result = get_stored_result(normalized_code)
    if stored_result:
        print(f"Found stored result for {normalized_code} (from {RESULTS_JSON_FILE})")
        print_quantitative_result(stored_result)
        return 0 if not stored_result.error_message else 1
    
    # Perform new search
    result = search_hts_quantitative_details(hts_code)
    
    # Save result to JSON if it's new and successful
    if not result.error_message:
        saved = save_result_to_json(result)
        if saved:
            print(f"\nResult saved to {RESULTS_JSON_FILE}")
        else:
            print(f"\nResult already exists in {RESULTS_JSON_FILE}")
    else:
        print(f"\nSkipping save due to error: {result.error_message}")
    
    print_quantitative_result(result)
    
    return 0 if not result.error_message else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

