"""
AWS Resource Scanner Plugin Architecture.

This module provides a base class for AWS resource scanners, enabling
easy extension to new resource types (EC2, Lambda, S3, RDS, etc.).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from enum import Enum
import boto3
import logging
from functools import lru_cache


logger = logging.getLogger(__name__)


class ResourceType(Enum):
    """Supported AWS resource types."""
    EC2 = "EC2"
    LAMBDA = "Lambda"
    RDS = "RDS"
    S3 = "S3"
    EBS = "EBS"


@dataclass
class AWSResource:
    """Represents an AWS resource with its tagging status.

    Attributes:
        resource_id: The unique identifier (instance ID, function name, etc.)
        resource_type: Type of resource
        region: AWS region
        tags: Dictionary of current tags
        state: Resource state (running, stopped, etc.)
        metadata: Additional resource-specific metadata
    """
    resource_id: str
    resource_type: ResourceType
    region: str
    tags: Dict[str, str]
    state: str
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}

    @property
    def is_tagged(self) -> bool:
        """Check if resource has any tags."""
        return len(self.tags) > 0

    @property
    def has_required_tags(self, required_tags: List[str]) -> bool:
        """Check if resource has all required tags.

        Args:
            required_tags: List of required tag keys

        Returns:
            True if all required tags are present
        """
        return all(tag in self.tags for tag in required_tags)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "resource_id": self.resource_id,
            "resource_type": self.resource_type.value,
            "region": self.region,
            "tags": self.tags,
            "state": self.state,
            "metadata": self.metadata
        }


@dataclass
class ScanResult:
    """Results from scanning a set of AWS resources.

    Attributes:
        resource_type: Type of resources scanned
        region: AWS region scanned
        total_resources: Total number of resources found
        tagged_resources: Resources with at least one tag
        untagged_resources: Resources with no tags
        resources: List of all scanned resources
    """
    resource_type: ResourceType
    region: str
    total_resources: int
    tagged_resources: List[AWSResource]
    untagged_resources: List[AWSResource]
    resources: List[AWSResource]

    @property
    def tagging_compliance_rate(self) -> float:
        """Calculate the percentage of tagged resources."""
        if self.total_resources == 0:
            return 0.0
        return (len(self.tagged_resources) / self.total_resources) * 100


class BaseResourceScanner(ABC):
    """Abstract base class for AWS resource scanners.

    All resource type scanners (EC2, Lambda, S3, etc.) should inherit from
    this class and implement the required methods.

    This provides a consistent interface for:
    - Scanning resources in a region
    - Filtering by tag status
    - Applying tags
    - Caching scan results
    """

    def __init__(self, region: str, profile: Optional[str] = None):
        """Initialize the resource scanner.

        Args:
            region: AWS region to scan
            profile: AWS profile name (optional)
        """
        self.region = region
        self.profile = profile
        self._session = None
        self._client = None

    @property
    def session(self) -> boto3.Session:
        """Get or create boto3 session."""
        if self._session is None:
            if self.profile:
                self._session = boto3.Session(
                    profile_name=self.profile,
                    region_name=self.region
                )
            else:
                self._session = boto3.Session(region_name=self.region)
        return self._session

    @property
    @abstractmethod
    def client(self):
        """Get or create AWS service client.

        Each scanner must implement this to return the appropriate
        boto3 client (ec2, lambda, s3, etc.).
        """
        pass

    @abstractmethod
    def scan(self) -> ScanResult:
        """Scan for resources in the configured region.

        Returns:
            ScanResult containing all found resources

        Raises:
            AWSError: If the scan fails
        """
        pass

    @abstractmethod
    def apply_tags(self, resource_id: str, tags: Dict[str, str]) -> bool:
        """Apply tags to a specific resource.

        Args:
            resource_id: The resource identifier
            tags: Dictionary of tags to apply

        Returns:
            True if successful, False otherwise

        Raises:
            AWSError: If tagging fails
        """
        pass

    @abstractmethod
    def get_resource_type(self) -> ResourceType:
        """Get the resource type this scanner handles.

        Returns:
            ResourceType enum value
        """
        pass

    def filter_untagged(self, resources: List[AWSResource]) -> List[AWSResource]:
        """Filter resources to only those without tags.

        Args:
            resources: List of resources to filter

        Returns:
            List of untagged resources
        """
        return [r for r in resources if not r.is_tagged]

    def filter_missing_required_tags(
        self,
        resources: List[AWSResource],
        required_tags: List[str]
    ) -> List[AWSResource]:
        """Filter resources missing any required tags.

        Args:
            resources: List of resources to filter
            required_tags: List of required tag keys

        Returns:
            List of resources missing required tags
        """
        return [r for r in resources if not r.has_required_tags(required_tags)]

    def __repr__(self) -> str:
        """Developer-friendly representation."""
        return (
            f"{self.__class__.__name__}("
            f"resource_type={self.get_resource_type().value}, "
            f"region={self.region})"
        )
