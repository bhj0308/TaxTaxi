"""
HTS Mapping Lookup (USITC HTS) with Browse Tree Support

Goal:
- If user enters an HTS code (full or partial): return matching HTS codes + descriptions
  PLUS full hierarchical browse tree (parent headings, subheadings, context).
  Examples: "61", "6109", "6109.10", "0901.21.00"
- If user enters a description/keyword: return matching HTS codes + descriptions.
  Examples: "clothes", "coffee", "roasted coffee", "food"
- Hybrid mode: HTS prefix + keyword filter (also includes browse tree)
  Examples: "61 cotton", "09 coffee"

Data source:
- USITC HTS REST endpoints:
  - Search:     https://hts.usitc.gov/reststop/search?keyword=...
  - ExportList: https://hts.usitc.gov/reststop/exportList?format=JSON&from=...&to=...&styles=false

Features:
- Range-based enumeration for exhaustive HTS prefix lookups
- Full hierarchical browse tree for all HTS queries (not just exact codes)
- Error handling with retries
- Hybrid mode (HTS prefix + keyword filter)
"""

from __future__ import annotations

import re
import sys
import time
import requests
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

SEARCH_URL = "https://hts.usitc.gov/reststop/search"
EXPORTLIST_URL = "https://hts.usitc.gov/reststop/exportList"

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 1.0
REQUEST_TIMEOUT = 30

# Toggle: if user enters an HTS code/prefix, fetch full hierarchical context (browse tree)
INCLUDE_CONTEXT = True

# HTS code patterns
HTS_CHARS_ONLY = re.compile(r"^[\d.\s]+$")  # digits, dots, spaces only
HYBRID_PATTERN = re.compile(r"^([\d.]+)\s+(.+)$")  # e.g., "61 cotton" -> ("61", "cotton")


@dataclass
class MappingCandidate:
    htsno: str
    description: str


@dataclass
class LookupResult:
    mode: str  # "hts_prefix" | "hts_range" | "keyword" | "exact_hts" | "hybrid" | "error"
    query: str
    count: int
    candidates: List[MappingCandidate] = field(default_factory=list)
    context_rows: Optional[List[Dict[str, Any]]] = None
    error_message: Optional[str] = None


@dataclass
class APIError(Exception):
    """Custom exception for API errors with context."""
    message: str
    status_code: Optional[int] = None
    retries_attempted: int = 0


# ----------------------------
# Normalization helpers
# ----------------------------

def _clean_text(s: Optional[str]) -> str:
    return (s or "").strip()


def normalize_hts_input(q: str) -> str:
    """Keep digits and dots; remove spaces; strip trailing dots."""
    q = _clean_text(q).replace(" ", "")
    q = q.rstrip(".")
    return q


def is_hts_like(q: str) -> bool:
    """Determine if user input looks like an HTS code/prefix (digits/dots only)."""
    q = _clean_text(q)
    if not q:
        return False
    return bool(HTS_CHARS_ONLY.match(q))


def hts_digits_len(q: str) -> int:
    return len(normalize_hts_input(q).replace(".", ""))


def is_exact_hts_code(q: str) -> bool:
    """
    Treat as exact HTS when it matches typical full forms:
      - 4 digits (heading)
      - 6 digits (subheading)
      - 8 digits (U.S. tariff line)
      - 10 digits (statistical suffix)
    """
    qn = normalize_hts_input(q)
    digits = qn.replace(".", "")
    if not digits.isdigit():
        return False
    return len(digits) in (4, 6, 8, 10)


def parse_hybrid_query(q: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse hybrid queries like "61 cotton" into (hts_prefix, keyword).
    Returns (None, None) if not a hybrid query.
    """
    q = _clean_text(q)
    match = HYBRID_PATTERN.match(q)
    if match:
        hts_part = match.group(1)
        keyword_part = match.group(2).strip()
        # Validate HTS part is numeric
        if hts_part.replace(".", "").isdigit() and keyword_part:
            return (normalize_hts_input(hts_part), keyword_part)
    return (None, None)


def build_hts_range(prefix: str) -> Tuple[str, str]:
    """
    Build a from/to range for exhaustive enumeration of an HTS prefix.
    
    Examples:
        "61"       -> ("6100.00.00.00", "6199.99.99.99")
        "6109"     -> ("6109.00.00.00", "6109.99.99.99")
        "6109.10"  -> ("6109.10.00.00", "6109.10.99.99")
        "0901.21"  -> ("0901.21.00.00", "0901.21.99.99")
    """
    prefix = normalize_hts_input(prefix)
    digits = prefix.replace(".", "")
    
    # Pad to get the "from" code (fill with 0s)
    # Pad to get the "to" code (fill with 9s)
    # Full HTS is 10 digits: XXXX.XX.XX.XX
    
    if len(digits) >= 10:
        # Already full code
        return (prefix, prefix)
    
    remaining = 10 - len(digits)
    from_digits = digits + "0" * remaining
    to_digits = digits + "9" * remaining
    
    # Format as XXXX.XX.XX.XX
    def format_hts(d: str) -> str:
        if len(d) >= 10:
            return f"{d[:4]}.{d[4:6]}.{d[6:8]}.{d[8:10]}"
        elif len(d) >= 8:
            return f"{d[:4]}.{d[4:6]}.{d[6:8]}"
        elif len(d) >= 6:
            return f"{d[:4]}.{d[4:6]}"
        elif len(d) >= 4:
            return d[:4]
        else:
            return d
    
    return (format_hts(from_digits), format_hts(to_digits))


# ----------------------------
# HTTP fetchers with retry
# ----------------------------

def _request_with_retry(
    method: str,
    url: str,
    params: Dict[str, Any],
    max_retries: int = MAX_RETRIES,
    timeout: int = REQUEST_TIMEOUT
) -> requests.Response:
    """
    Make HTTP request with retry logic and exponential backoff.
    """
    last_error = None
    
    for attempt in range(max_retries):
        try:
            if method.upper() == "GET":
                response = requests.get(url, params=params, timeout=timeout)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            response.raise_for_status()
            return response
            
        except requests.exceptions.Timeout as e:
            last_error = APIError(
                message=f"Request timed out after {timeout}s",
                retries_attempted=attempt + 1
            )
        except requests.exceptions.ConnectionError as e:
            last_error = APIError(
                message=f"Connection error: {str(e)}",
                retries_attempted=attempt + 1
            )
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else None
            # Don't retry on 4xx client errors (except 429 rate limit)
            if status_code and 400 <= status_code < 500 and status_code != 429:
                raise APIError(
                    message=f"HTTP {status_code}: {str(e)}",
                    status_code=status_code,
                    retries_attempted=attempt + 1
                )
            last_error = APIError(
                message=f"HTTP error: {str(e)}",
                status_code=status_code,
                retries_attempted=attempt + 1
            )
        except requests.exceptions.RequestException as e:
            last_error = APIError(
                message=f"Request failed: {str(e)}",
                retries_attempted=attempt + 1
            )
        
        # Wait before retry (exponential backoff)
        if attempt < max_retries - 1:
            delay = RETRY_DELAY_SECONDS * (2 ** attempt)
            time.sleep(delay)
    
    # All retries exhausted
    raise last_error or APIError(message="Unknown error after retries", retries_attempted=max_retries)


def fetch_search(keyword: str) -> List[Dict[str, Any]]:
    """Call /reststop/search with retry. Returns raw list of items."""
    try:
        response = _request_with_retry("GET", SEARCH_URL, {"keyword": keyword})
        data = response.json()
        if isinstance(data, list):
            return data
        return []
    except APIError:
        raise
    except Exception as e:
        raise APIError(message=f"Failed to parse search response: {str(e)}")


def fetch_exportlist(from_code: str, to_code: str) -> List[Dict[str, Any]]:
    """Call /reststop/exportList with retry. Returns raw list of items."""
    params = {
        "format": "JSON",
        "from": from_code,
        "to": to_code,
        "styles": "false",
    }
    try:
        response = _request_with_retry("GET", EXPORTLIST_URL, params)
        data = response.json()
        if isinstance(data, list):
            return data
        return []
    except APIError:
        raise
    except Exception as e:
        raise APIError(message=f"Failed to parse exportList response: {str(e)}")


def fetch_range_enumeration(prefix: str) -> List[Dict[str, Any]]:
    """
    Fetch ALL HTS codes under a prefix using range-based exportList.
    This is more exhaustive than /search.
    """
    from_code, to_code = build_hts_range(prefix)
    return fetch_exportlist(from_code, to_code)


# ----------------------------
# Core mapping logic
# ----------------------------

def extract_candidates_from_items(data: List[Dict[str, Any]]) -> List[MappingCandidate]:
    """Convert raw API results to MappingCandidate list."""
    out: List[MappingCandidate] = []
    for item in data:
        code = _clean_text(item.get("htsno"))
        desc = _clean_text(item.get("description"))
        # Include items with description even if code is empty (header rows)
        # But for mapping, we want actual codes
        if code and desc:
            out.append(MappingCandidate(htsno=code, description=desc))
    return out


def dedupe_candidates(cands: List[MappingCandidate]) -> List[MappingCandidate]:
    """Deduplicate by HTS code (keep first occurrence)."""
    seen = set()
    out = []
    for c in cands:
        if c.htsno not in seen:
            out.append(c)
            seen.add(c.htsno)
    return out


def filter_by_hts_prefix(cands: List[MappingCandidate], prefix: str) -> List[MappingCandidate]:
    """Keep candidates whose HTS code starts with the prefix."""
    prefix = normalize_hts_input(prefix)
    if not prefix:
        return cands
    
    prefix_digits = prefix.replace(".", "")
    
    out = []
    for c in cands:
        code_digits = c.htsno.replace(".", "")
        if c.htsno.startswith(prefix) or code_digits.startswith(prefix_digits):
            out.append(c)
    return out


def filter_by_keyword(cands: List[MappingCandidate], keyword: str) -> List[MappingCandidate]:
    """Filter candidates by keyword match in description (case-insensitive)."""
    keyword = keyword.lower().strip()
    if not keyword:
        return cands
    
    # Support multiple keywords (AND logic)
    keywords = keyword.split()
    
    out = []
    for c in cands:
        desc_lower = c.description.lower()
        # All keywords must be present
        if all(kw in desc_lower for kw in keywords):
            out.append(c)
    return out


def mapping_lookup(query: str, max_results: int = 500, use_range: bool = True) -> LookupResult:
    """
    Main entry point for HTS mapping lookup with full browse tree support.
    
    For HTS code/prefix queries, returns both:
    - candidates: List of (code, description) mappings
    - context_rows: Full hierarchical browse tree (parent headings, subheadings, etc.)
    
    Args:
        query: User input - can be HTS code, keyword, or hybrid ("61 cotton")
        max_results: Maximum number of candidates to return
        use_range: If True, use range-based enumeration for HTS prefixes (more exhaustive)
                   and includes full hierarchical context in context_rows
    
    Returns:
        LookupResult with mode, candidates, context_rows (browse tree), and optional error info
    """
    q = _clean_text(query)
    if not q:
        return LookupResult(mode="empty", query="", count=0)
    
    # Check for hybrid query first (e.g., "61 cotton")
    hts_prefix, keyword_filter = parse_hybrid_query(q)
    if hts_prefix and keyword_filter:
        return _lookup_hybrid(hts_prefix, keyword_filter, max_results, use_range)
    
    # Pure HTS prefix/code
    if is_hts_like(q):
        return _lookup_hts(q, max_results, use_range)
    
    # Pure keyword search
    return _lookup_keyword(q, max_results)


def _lookup_hts(query: str, max_results: int, use_range: bool) -> LookupResult:
    """
    Handle pure HTS code/prefix lookup with full hierarchical context (browse tree).
    """
    qn = normalize_hts_input(query)
    
    try:
        context = None
        cands = []
        
        if use_range:
            # Use range-based enumeration for exhaustive results
            # This already returns the full hierarchical tree structure
            raw = fetch_range_enumeration(qn)
            
            # Extract candidates (codes with descriptions) for mapping
            cands = extract_candidates_from_items(raw)
            # Filter to ensure prefix match (exportList might return parent context)
            cands = filter_by_hts_prefix(cands, qn)
            
            # Preserve full hierarchical context for browse tree
            if INCLUDE_CONTEXT:
                context = raw  # Full tree structure from exportList
            
            mode = "hts_range"
        else:
            # Use search-based approach
            raw = fetch_search(qn)
            cands = extract_candidates_from_items(raw)
            cands = filter_by_hts_prefix(cands, qn)
            mode = "hts_prefix"
            
            # For search-based, fetch context separately if needed
            if INCLUDE_CONTEXT:
                try:
                    # Try to get context using range enumeration even in search mode
                    # This gives us the full tree structure
                    context = fetch_range_enumeration(qn)
                except APIError:
                    # Fallback: try exact code lookup if it's an exact code
                    if is_exact_hts_code(qn):
                        try:
                            context = fetch_exportlist(qn, qn)
                        except APIError:
                            context = None  # Non-fatal
        
        cands = dedupe_candidates(cands)
        
        # Determine if exact code
        if is_exact_hts_code(qn):
            mode = "exact_hts"
        
        return LookupResult(
            mode=mode,
            query=qn,
            count=len(cands),
            candidates=cands[:max_results],
            context_rows=context
        )
        
    except APIError as e:
        return LookupResult(
            mode="error",
            query=qn,
            count=0,
            error_message=f"{e.message} (retries: {e.retries_attempted})"
        )


def _lookup_keyword(query: str, max_results: int) -> LookupResult:
    """Handle pure keyword lookup."""
    try:
        raw = fetch_search(query)
        cands = extract_candidates_from_items(raw)
        cands = dedupe_candidates(cands)
        
        return LookupResult(
            mode="keyword",
            query=query,
            count=len(cands),
            candidates=cands[:max_results]
        )
        
    except APIError as e:
        return LookupResult(
            mode="error",
            query=query,
            count=0,
            error_message=f"{e.message} (retries: {e.retries_attempted})"
        )


def _lookup_hybrid(hts_prefix: str, keyword: str, max_results: int, use_range: bool) -> LookupResult:
    """
    Handle hybrid lookup: HTS prefix + keyword filter.
    Example: "61 cotton" -> all 61xx codes containing "cotton"
    Also includes full hierarchical context for browse tree.
    """
    try:
        context = None
        
        if use_range:
            # Get all codes under the prefix (includes full tree)
            raw = fetch_range_enumeration(hts_prefix)
            cands = extract_candidates_from_items(raw)
            cands = filter_by_hts_prefix(cands, hts_prefix)
            
            # Preserve full hierarchical context
            if INCLUDE_CONTEXT:
                context = raw
        else:
            raw = fetch_search(hts_prefix)
            cands = extract_candidates_from_items(raw)
            cands = filter_by_hts_prefix(cands, hts_prefix)
            
            # Try to get context for hybrid queries too
            if INCLUDE_CONTEXT:
                try:
                    context = fetch_range_enumeration(hts_prefix)
                except APIError:
                    context = None
        
        # Apply keyword filter
        cands = filter_by_keyword(cands, keyword)
        cands = dedupe_candidates(cands)
        
        return LookupResult(
            mode="hybrid",
            query=f"{hts_prefix} + '{keyword}'",
            count=len(cands),
            candidates=cands[:max_results],
            context_rows=context
        )
        
    except APIError as e:
        return LookupResult(
            mode="error",
            query=f"{hts_prefix} {keyword}",
            count=0,
            error_message=f"{e.message} (retries: {e.retries_attempted})"
        )


# ----------------------------
# Convenience functions
# ----------------------------

def hts_to_descriptions(hts_code: str, max_results: int = 500) -> List[Tuple[str, str]]:
    """
    Simple function: HTS code/prefix -> list of (code, description) tuples.
    
    Examples:
        hts_to_descriptions("61")
        hts_to_descriptions("6109.10")
        hts_to_descriptions("0901.21.00")
    """
    result = mapping_lookup(hts_code, max_results=max_results, use_range=True)
    return [(c.htsno, c.description) for c in result.candidates]


def description_to_hts(keyword: str, max_results: int = 200) -> List[Tuple[str, str]]:
    """
    Simple function: keyword/description -> list of (code, description) tuples.
    
    Examples:
        description_to_hts("cotton shirts")
        description_to_hts("roasted coffee")
    """
    result = mapping_lookup(keyword, max_results=max_results, use_range=False)
    return [(c.htsno, c.description) for c in result.candidates]


def hts_with_filter(hts_prefix: str, keyword: str, max_results: int = 500) -> List[Tuple[str, str]]:
    """
    Simple function: HTS prefix + keyword filter -> list of (code, description) tuples.
    
    Examples:
        hts_with_filter("61", "cotton")  # All 61xx codes with "cotton"
        hts_with_filter("09", "coffee")  # All 09xx codes with "coffee"
    """
    result = _lookup_hybrid(hts_prefix, keyword, max_results, use_range=True)
    return [(c.htsno, c.description) for c in result.candidates]


# ----------------------------
# Pretty printing
# ----------------------------

def print_lookup_result(res: LookupResult) -> None:
    print(f"Mode: {res.mode}")
    print(f"Query: {res.query}")
    
    if res.error_message:
        print(f"Error: {res.error_message}")
        return
    
    print(f"Candidates: {res.count}")
    print("-" * 70)
    
    for c in res.candidates:
        # Truncate long descriptions for display
        desc = c.description
        if len(desc) > 60:
            desc = desc[:57] + "..."
        print(f"{c.htsno:20} {desc}")
    
    print("-" * 70)
    
    if res.context_rows:
        print(f"\nBrowse Tree (hierarchical context, showing {min(len(res.context_rows), 100)} of {len(res.context_rows)} rows):")
        print("=" * 70)
        for item in res.context_rows[:100]:  # Show up to 100 rows of context
            code = _clean_text(item.get("htsno"))
            desc = _clean_text(item.get("description"))
            indent = int(item.get("indent", 0) or 0)
            
            # Show header rows (superior/indent rows without codes)
            if not code and (item.get("superior") == "true" or indent > 0):
                print("  " * indent + f"── {desc} ──")
            elif code:
                # Show codes with descriptions
                short_desc = desc[:55] + "..." if len(desc) > 55 else desc
                print("  " * indent + f"{code:20} {short_desc}")
            elif desc:
                # Fallback for any other rows with descriptions
                print("  " * indent + f"    {desc}")
        
        if len(res.context_rows) > 100:
            print(f"\n... ({len(res.context_rows) - 100} more rows in full tree)")
        print("=" * 70)


# ----------------------------
# CLI usage
# ----------------------------

def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print("HTS Mapping Lookup")
        print("=" * 50)
        print("\nUsage:")
        print("  python usitc_search.py <query>")
        print("\nExamples:")
        print("  python usitc_search.py 61              # All codes under chapter 61")
        print("  python usitc_search.py 6109.10         # Codes under 6109.10")
        print("  python usitc_search.py 0901.21.00      # Exact code with context")
        print("  python usitc_search.py coffee          # Keyword search")
        print("  python usitc_search.py \"61 cotton\"     # Hybrid: 61xx + cotton")
        print("  python usitc_search.py \"09 roasted\"    # Hybrid: 09xx + roasted")
        return 1
    
    query = " ".join(argv[1:]).strip()
    print(f"\nSearching: {query}\n")
    
    res = mapping_lookup(query=query, max_results=200)
    print_lookup_result(res)
    
    return 0 if res.mode != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
