"""
Context Tracker for AWS TagSense.

This module tracks the AWS environment context (regions scanned, profiles used,
scan results, etc.) to provide better contextual assistance in the chat interface.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
import json


@dataclass
class ScanResult:
    """Represents the results of an AWS resource scan.

    Attributes:
        timestamp: When the scan was performed
        region: AWS region scanned
        profile: AWS profile used
        resource_type: Type of resources scanned (EC2, Lambda, etc.)
        total_resources: Total number of resources found
        untagged_resources: Number of untagged resources
        resource_ids: List of resource IDs (sample or all)
        metadata: Additional scan metadata
    """
    timestamp: datetime
    region: str
    profile: str
    resource_type: str
    total_resources: int
    untagged_resources: int
    resource_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "region": self.region,
            "profile": self.profile,
            "resource_type": self.resource_type,
            "total_resources": self.total_resources,
            "untagged_resources": self.untagged_resources,
            "resource_ids": self.resource_ids,
            "metadata": self.metadata
        }


class AWSContextTracker:
    """Tracks AWS environment context for better AI assistance.

    This class maintains context about:
    - Which regions have been scanned
    - Which AWS profiles have been used
    - Recent scan results
    - Resource inventory

    This context helps the AI provide more relevant, environment-specific advice.

    Example:
        ```python
        tracker = AWSContextTracker()

        # Record a scan
        tracker.record_scan(
            region="us-east-1",
            profile="production",
            resource_type="EC2",
            total=100,
            untagged=25,
            resource_ids=["i-123", "i-456"]
        )

        # Get context for AI
        context = tracker.get_context_summary()
        ```
    """

    def __init__(self):
        """Initialize the context tracker."""
        self.scan_history: List[ScanResult] = []
        self.regions_scanned: set = set()
        self.profiles_used: set = set()
        self.resource_inventory: Dict[str, Dict] = {}  # resource_type -> stats

    def record_scan(
        self,
        region: str,
        profile: str,
        resource_type: str,
        total_resources: int,
        untagged_resources: int,
        resource_ids: Optional[List[str]] = None,
        metadata: Optional[Dict] = None
    ) -> None:
        """Record a scan operation.

        Args:
            region: AWS region
            profile: AWS profile name
            resource_type: Type of resource scanned
            total_resources: Total count of resources
            untagged_resources: Count of untagged resources
            resource_ids: Optional list of resource IDs
            metadata: Optional additional metadata
        """
        scan = ScanResult(
            timestamp=datetime.now(),
            region=region,
            profile=profile,
            resource_type=resource_type,
            total_resources=total_resources,
            untagged_resources=untagged_resources,
            resource_ids=resource_ids or [],
            metadata=metadata or {}
        )

        self.scan_history.append(scan)
        self.regions_scanned.add(region)
        self.profiles_used.add(profile)

        # Update inventory
        if resource_type not in self.resource_inventory:
            self.resource_inventory[resource_type] = {
                "total": 0,
                "untagged": 0,
                "last_scan": None
            }

        self.resource_inventory[resource_type].update({
            "total": total_resources,
            "untagged": untagged_resources,
            "last_scan": scan.timestamp.isoformat()
        })

    def get_latest_scan(self, resource_type: Optional[str] = None) -> Optional[ScanResult]:
        """Get the most recent scan result.

        Args:
            resource_type: Optional filter by resource type

        Returns:
            Latest scan result or None
        """
        if not self.scan_history:
            return None

        if resource_type:
            filtered = [s for s in self.scan_history if s.resource_type == resource_type]
            return filtered[-1] if filtered else None

        return self.scan_history[-1]

    def get_context_summary(self) -> str:
        """Get a text summary of the current AWS context.

        Returns:
            Human-readable context summary
        """
        if not self.scan_history:
            return "No AWS scans performed yet."

        latest = self.scan_history[-1]

        summary_parts = [
            f"**Current AWS Context:**",
            f"- Last Scan: {latest.resource_type} in {latest.region}",
            f"- Found: {latest.total_resources} total, {latest.untagged_resources} untagged",
            f"- Regions Scanned: {', '.join(sorted(self.regions_scanned))}",
            f"- AWS Profiles: {', '.join(sorted(self.profiles_used))}"
        ]

        if self.resource_inventory:
            summary_parts.append("\n**Resource Inventory:**")
            for resource_type, stats in self.resource_inventory.items():
                summary_parts.append(
                    f"- {resource_type}: {stats['total']} total ({stats['untagged']} untagged)"
                )

        return "\n".join(summary_parts)

    def get_context_for_prompt(self) -> str:
        """Get context formatted for inclusion in an LLM prompt.

        Returns:
            Context string suitable for LLM prompt
        """
        if not self.scan_history:
            return ""

        latest = self.scan_history[-1]

        context = f"""Recent AWS Scan Context:
- Region: {latest.region}
- Resource Type: {latest.resource_type}
- Total Resources: {latest.total_resources}
- Untagged Resources: {latest.untagged_resources}
- Tagging Compliance: {((latest.total_resources - latest.untagged_resources) / latest.total_resources * 100) if latest.total_resources > 0 else 0:.1f}%
"""

        if latest.resource_ids:
            sample_ids = latest.resource_ids[:5]
            context += f"\nSample Resource IDs: {', '.join(sample_ids)}"
            if len(latest.resource_ids) > 5:
                context += f" (and {len(latest.resource_ids) - 5} more)"

        return context

    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive statistics about scanned resources.

        Returns:
            Dictionary of statistics
        """
        if not self.scan_history:
            return {
                "total_scans": 0,
                "regions": [],
                "profiles": [],
                "resource_types": []
            }

        total_resources = sum(inv["total"] for inv in self.resource_inventory.values())
        total_untagged = sum(inv["untagged"] for inv in self.resource_inventory.values())

        return {
            "total_scans": len(self.scan_history),
            "regions": list(self.regions_scanned),
            "profiles": list(self.profiles_used),
            "resource_types": list(self.resource_inventory.keys()),
            "inventory": self.resource_inventory,
            "totals": {
                "total_resources": total_resources,
                "total_untagged": total_untagged,
                "tagging_compliance_pct": (
                    (total_resources - total_untagged) / total_resources * 100
                    if total_resources > 0 else 0
                )
            }
        }

    def clear(self) -> None:
        """Clear all tracked context."""
        self.scan_history.clear()
        self.regions_scanned.clear()
        self.profiles_used.clear()
        self.resource_inventory.clear()

    def export_json(self) -> str:
        """Export context as JSON.

        Returns:
            JSON string representation
        """
        return json.dumps({
            "scan_history": [scan.to_dict() for scan in self.scan_history],
            "regions_scanned": list(self.regions_scanned),
            "profiles_used": list(self.profiles_used),
            "resource_inventory": self.resource_inventory,
            "statistics": self.get_statistics()
        }, indent=2)

    def __len__(self) -> int:
        """Get number of scans recorded."""
        return len(self.scan_history)

    def __repr__(self) -> str:
        """Developer-friendly representation."""
        return (
            f"AWSContextTracker("
            f"scans={len(self.scan_history)}, "
            f"regions={len(self.regions_scanned)}, "
            f"resource_types={len(self.resource_inventory)})"
        )
