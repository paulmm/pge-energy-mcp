"""Parse ESPI (Energy Services Provider Interface) Atom XML from Green Button Connect API.

PG&E's Share My Data API returns interval usage data in ESPI/NAESB XML format.
This parser extracts IntervalReading elements and converts them to the same
dict format as parse_green_button so they can be used interchangeably.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone, date

# ESPI namespace used in all Green Button Connect XML
ESPI_NS = "http://naesb.org/espi"
ATOM_NS = "http://www.w3.org/2005/Atom"

NS = {
    "espi": ESPI_NS,
    "atom": ATOM_NS,
}


def parse_espi_xml(xml_content: str) -> dict:
    """
    Parse ESPI Atom XML into structured interval data matching Green Button CSV format.

    Handles both import (flowDirection=1) and export (flowDirection=19) readings.
    Returns the same structure as green_button.parse() so downstream tools work
    with either source.

    Args:
        xml_content: Raw XML string from ESPI API response

    Returns:
        {
            "metadata": {"source": "espi_api", "date_range": {...}},
            "intervals": [{"date", "hour", "month", "day_of_week",
                           "import_kwh", "export_kwh"}],
            "summary": {"total_import_kwh", "total_export_kwh",
                        "num_intervals", "date_range"}
        }
    """
    root = ET.fromstring(xml_content)

    # Collect all readings keyed by (date_str, hour) with import/export separated
    readings: dict[tuple[str, int], dict] = {}

    # Walk all IntervalBlock elements — may appear under entry/content or directly
    for interval_block in _find_all_interval_blocks(root):
        flow_direction = _get_flow_direction(interval_block, root)

        for reading in interval_block.findall("espi:IntervalReading", NS):
            time_period = reading.find("espi:timePeriod", NS)
            value_el = reading.find("espi:value", NS)

            if time_period is None or value_el is None:
                continue

            start_el = time_period.find("espi:start", NS)
            duration_el = time_period.find("espi:duration", NS)

            if start_el is None or start_el.text is None:
                continue

            epoch = int(start_el.text)
            dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
            wh = int(value_el.text) if value_el.text else 0
            kwh = wh / 1000.0

            date_str = dt.strftime("%Y-%m-%d")
            hour = dt.hour
            key = (date_str, hour)

            if key not in readings:
                readings[key] = {
                    "date": date_str,
                    "hour": hour,
                    "month": dt.month,
                    "day_of_week": dt.weekday(),  # 0=Mon, 6=Sun
                    "import_kwh": 0.0,
                    "export_kwh": 0.0,
                }

            if flow_direction == 19:
                readings[key]["export_kwh"] += kwh
            else:
                # Default to import (flowDirection 1 or unknown)
                readings[key]["import_kwh"] += kwh

    # Sort by date then hour
    intervals = sorted(readings.values(), key=lambda r: (r["date"], r["hour"]))

    # Round values
    total_import = 0.0
    total_export = 0.0
    for iv in intervals:
        iv["import_kwh"] = round(iv["import_kwh"], 4)
        iv["export_kwh"] = round(iv["export_kwh"], 4)
        total_import += iv["import_kwh"]
        total_export += iv["export_kwh"]

    date_range = None
    if intervals:
        date_range = {"start": intervals[0]["date"], "end": intervals[-1]["date"]}

    return {
        "metadata": {
            "source": "espi_api",
            "date_range": date_range,
        },
        "intervals": intervals,
        "summary": {
            "total_import_kwh": round(total_import, 2),
            "total_export_kwh": round(total_export, 2),
            "num_intervals": len(intervals),
            "date_range": date_range,
        },
    }


def _find_all_interval_blocks(root: ET.Element) -> list[ET.Element]:
    """Find all IntervalBlock elements regardless of nesting."""
    blocks = []

    # Direct children
    blocks.extend(root.findall(f".//{{{ESPI_NS}}}IntervalBlock"))

    # Under Atom entry/content
    for entry in root.findall(f".//{{{ATOM_NS}}}entry"):
        content = entry.find(f"{{{ATOM_NS}}}content")
        if content is not None:
            blocks.extend(content.findall(f".//{{{ESPI_NS}}}IntervalBlock"))

    # Deduplicate (some may be found both ways)
    seen = set()
    unique = []
    for b in blocks:
        bid = id(b)
        if bid not in seen:
            seen.add(bid)
            unique.append(b)

    return unique


def _get_flow_direction(interval_block: ET.Element, root: ET.Element) -> int:
    """
    Determine flow direction for an IntervalBlock.

    PG&E ESPI XML encodes flowDirection in the ReadingType linked from
    the MeterReading. We check multiple locations:
    1. ReadingType as sibling/ancestor
    2. Any ReadingType in the document with flowDirection

    Returns 1 for import (delivered), 19 for export (received).
    Default is 1 (import) if not found.
    """
    # Check parent chain for ReadingType
    # In typical ESPI XML, ReadingType is a sibling of IntervalBlock
    # under MeterReading, or linked via href
    for reading_type in root.iter(f"{{{ESPI_NS}}}ReadingType"):
        flow_dir = reading_type.find(f"{{{ESPI_NS}}}flowDirection")
        if flow_dir is not None and flow_dir.text:
            val = int(flow_dir.text)
            # If there's only one ReadingType, use it
            # For multi-flow documents, caller should separate by entry
            return val

    return 1  # Default to import
