"""
EC2 Instance Scanner for AWS TagSense.

This module provides enhanced EC2 instance scanning with caching,
error handling, and multi-region support.
"""

from typing import List, Dict, Optional
import logging
from functools import lru_cache
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)
from botocore.exceptions import ClientError, BotoCoreError

from tagger_core.resource_scanner import (
    BaseResourceScanner,
    AWSResource,
    ScanResult,
    ResourceType
)


logger = logging.getLogger(__name__)


class EC2Scanner(BaseResourceScanner):
    """Scanner for EC2 instances.

    Provides enhanced functionality for scanning EC2 instances including:
    - Automatic retries with exponential backoff
    - Response caching
    - Detailed error handling
    - Filtering by state (running, stopped, etc.)

    Example:
        ```python
        scanner = EC2Scanner(region="us-east-1", profile="production")
        result = scanner.scan()

        print(f"Found {result.total_resources} EC2 instances")
        print(f"Untagged: {len(result.untagged_resources)}")

        # Tag untagged instances
        for instance in result.untagged_resources:
            scanner.apply_tags(instance.resource_id, {
                "Environment": "production",
                "ManagedBy": "aws-tagsense"
            })
        ```
    """

    @property
    def client(self):
        """Get or create EC2 client."""
        if self._client is None:
            self._client = self.session.client('ec2')
        return self._client

    def get_resource_type(self) -> ResourceType:
        """Get the resource type.

        Returns:
            ResourceType.EC2
        """
        return ResourceType.EC2

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(ClientError),
        reraise=True
    )
    def scan(self, state_filter: Optional[List[str]] = None) -> ScanResult:
        """Scan for EC2 instances in the region.

        Args:
            state_filter: Optional list of instance states to filter by
                          (e.g., ['running', 'stopped']). If None, scans all states.

        Returns:
            ScanResult containing all found instances

        Raises:
            ClientError: If the AWS API call fails after retries
        """
        try:
            logger.info(f"Scanning EC2 instances in {self.region}")

            # Build filters
            filters = []
            if state_filter:
                filters.append({
                    'Name': 'instance-state-name',
                    'Values': state_filter
                })

            # Describe instances
            if filters:
                response = self.client.describe_instances(Filters=filters)
            else:
                response = self.client.describe_instances()

            # Parse instances
            instances = []
            for reservation in response.get('Reservations', []):
                for instance_data in reservation.get('Instances', []):
                    instance = self._parse_instance(instance_data)
                    instances.append(instance)

            # Separate tagged and untagged
            tagged = [i for i in instances if i.is_tagged]
            untagged = [i for i in instances if not i.is_tagged]

            logger.info(
                f"EC2 scan complete: {len(instances)} total, "
                f"{len(untagged)} untagged"
            )

            return ScanResult(
                resource_type=ResourceType.EC2,
                region=self.region,
                total_resources=len(instances),
                tagged_resources=tagged,
                untagged_resources=untagged,
                resources=instances
            )

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(f"EC2 scan failed: {error_code} - {str(e)}")
            raise

        except Exception as e:
            logger.error(f"Unexpected error during EC2 scan: {str(e)}")
            raise

    def _parse_instance(self, instance_data: Dict) -> AWSResource:
        """Parse EC2 instance data into AWSResource object.

        Args:
            instance_data: Raw instance data from AWS API

        Returns:
            AWSResource object
        """
        instance_id = instance_data['InstanceId']
        state = instance_data['State']['Name']

        # Parse tags
        tags = {}
        for tag in instance_data.get('Tags', []):
            tags[tag['Key']] = tag['Value']

        # Collect metadata
        metadata = {
            'instance_type': instance_data.get('InstanceType'),
            'launch_time': instance_data.get('LaunchTime').isoformat() if instance_data.get('LaunchTime') else None,
            'availability_zone': instance_data.get('Placement', {}).get('AvailabilityZone'),
            'vpc_id': instance_data.get('VpcId'),
            'subnet_id': instance_data.get('SubnetId'),
            'private_ip': instance_data.get('PrivateIpAddress'),
            'public_ip': instance_data.get('PublicIpAddress'),
            'platform': instance_data.get('Platform', 'linux'),
        }

        # Add name tag to metadata if present
        if 'Name' in tags:
            metadata['name'] = tags['Name']

        return AWSResource(
            resource_id=instance_id,
            resource_type=ResourceType.EC2,
            region=self.region,
            tags=tags,
            state=state,
            metadata=metadata
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(ClientError),
        reraise=True
    )
    def apply_tags(
        self,
        resource_id: str,
        tags: Dict[str, str],
        dry_run: bool = False
    ) -> bool:
        """Apply tags to an EC2 instance.

        Args:
            resource_id: EC2 instance ID
            tags: Dictionary of tags to apply
            dry_run: If True, validate without actually applying tags

        Returns:
            True if successful

        Raises:
            ClientError: If tagging fails
        """
        try:
            # Convert tags to AWS format
            aws_tags = [{"Key": k, "Value": v} for k, v in tags.items()]

            # Apply tags
            self.client.create_tags(
                Resources=[resource_id],
                Tags=aws_tags,
                DryRun=dry_run
            )

            if not dry_run:
                logger.info(f"Successfully tagged EC2 instance {resource_id} with {len(tags)} tags")

            return True

        except ClientError as e:
            if dry_run and e.response.get('Error', {}).get('Code') == 'DryRunOperation':
                # Dry run succeeded (would have worked)
                logger.info(f"Dry run successful for {resource_id}")
                return True

            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(f"Failed to tag EC2 instance {resource_id}: {error_code} - {str(e)}")
            raise

    def scan_running_only(self) -> ScanResult:
        """Convenience method to scan only running instances.

        Returns:
            ScanResult for running instances
        """
        return self.scan(state_filter=['running'])

    def scan_stopped_only(self) -> ScanResult:
        """Convenience method to scan only stopped instances.

        Returns:
            ScanResult for stopped instances
        """
        return self.scan(state_filter=['stopped'])

    def get_untagged_running_instances(self) -> List[AWSResource]:
        """Get untagged instances that are currently running.

        Returns:
            List of untagged running instances
        """
        result = self.scan_running_only()
        return result.untagged_resources
