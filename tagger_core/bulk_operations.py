"""
Bulk Tagging Operations for AWS TagSense.

Enterprise-grade bulk operations with:
- Transactional tagging with rollback
- Batch processing
- Progress tracking
- Error handling and recovery
- Dry-run mode
"""

import logging
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time
import json

from .resource_scanner import BaseResourceScanner, ResourceType, AWSResource
from .ec2_scanner import EC2Scanner
from .lambda_scanner import LambdaScanner
from .s3_scanner import S3Scanner
from .rds_scanner import RDSScanner
from .ebs_scanner import EBSScanner

logger = logging.getLogger(__name__)


class OperationStatus(Enum):
    """Status of a bulk operation."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


@dataclass
class TagOperation:
    """Single tag operation."""
    resource_id: str
    resource_type: ResourceType
    region: str
    tags_to_apply: Dict[str, str]
    original_tags: Dict[str, str] = field(default_factory=dict)
    status: OperationStatus = OperationStatus.PENDING
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class BulkTaggingResult:
    """Result of bulk tagging operation."""
    total_operations: int
    successful: int
    failed: int
    rolled_back: int
    operations: List[TagOperation] = field(default_factory=list)
    duration_seconds: float = 0.0
    dry_run: bool = False

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        if self.total_operations == 0:
            return 0.0
        return (self.successful / self.total_operations) * 100

    def get_failed_operations(self) -> List[TagOperation]:
        """Get all failed operations."""
        return [op for op in self.operations if op.status == OperationStatus.FAILED]

    def get_successful_operations(self) -> List[TagOperation]:
        """Get all successful operations."""
        return [op for op in self.operations if op.status == OperationStatus.COMPLETED]


class BulkTaggingEngine:
    """Engine for bulk tagging operations with transaction support."""

    def __init__(
        self,
        profile: Optional[str] = None,
        batch_size: int = 50,
        enable_rollback: bool = True
    ):
        """Initialize bulk tagging engine.

        Args:
            profile: AWS profile name
            batch_size: Number of resources to process in each batch
            enable_rollback: Enable automatic rollback on failure
        """
        self.profile = profile
        self.batch_size = batch_size
        self.enable_rollback = enable_rollback
        self._scanners = {}  # Cache scanners by (resource_type, region)

    def _get_scanner(
        self,
        resource_type: ResourceType,
        region: str
    ) -> BaseResourceScanner:
        """Get or create scanner for resource type and region.

        Args:
            resource_type: Type of resource
            region: AWS region

        Returns:
            Scanner instance
        """
        key = (resource_type, region)

        if key not in self._scanners:
            scanner_map = {
                ResourceType.EC2: EC2Scanner,
                ResourceType.LAMBDA: LambdaScanner,
                ResourceType.S3: S3Scanner,
                ResourceType.RDS: RDSScanner,
                ResourceType.EBS: EBSScanner
            }

            if resource_type not in scanner_map:
                raise ValueError(f"Unsupported resource type: {resource_type}")

            scanner_class = scanner_map[resource_type]
            self._scanners[key] = scanner_class(region=region, profile=self.profile)

        return self._scanners[key]

    def bulk_tag_resources(
        self,
        resources: List[AWSResource],
        tags: Dict[str, str],
        dry_run: bool = False,
        fail_fast: bool = False
    ) -> BulkTaggingResult:
        """Apply tags to multiple resources with transaction support.

        Args:
            resources: List of resources to tag
            tags: Tags to apply to all resources
            dry_run: If True, don't actually apply tags
            fail_fast: If True, stop on first error

        Returns:
            BulkTaggingResult with operation details
        """
        start_time = time.time()

        logger.info(
            f"Starting bulk tagging operation: {len(resources)} resources, "
            f"{len(tags)} tags, dry_run={dry_run}"
        )

        # Create operations
        operations = []
        for resource in resources:
            operation = TagOperation(
                resource_id=resource.resource_id,
                resource_type=resource.resource_type,
                region=resource.region,
                tags_to_apply=tags,
                original_tags=resource.tags.copy()
            )
            operations.append(operation)

        if dry_run:
            logger.info(f"DRY RUN: Would tag {len(operations)} resources")
            for op in operations:
                op.status = OperationStatus.COMPLETED

            return BulkTaggingResult(
                total_operations=len(operations),
                successful=len(operations),
                failed=0,
                rolled_back=0,
                operations=operations,
                duration_seconds=time.time() - start_time,
                dry_run=True
            )

        # Process operations in batches
        successful = 0
        failed = 0
        rolled_back = 0

        for i in range(0, len(operations), self.batch_size):
            batch = operations[i:i + self.batch_size]
            logger.info(f"Processing batch {i // self.batch_size + 1} ({len(batch)} resources)")

            for operation in batch:
                try:
                    operation.status = OperationStatus.IN_PROGRESS

                    # Get scanner
                    scanner = self._get_scanner(
                        operation.resource_type,
                        operation.region
                    )

                    # Apply tags
                    success = scanner.apply_tags(
                        operation.resource_id,
                        operation.tags_to_apply
                    )

                    if success:
                        operation.status = OperationStatus.COMPLETED
                        successful += 1
                        logger.debug(f"Tagged {operation.resource_id} successfully")
                    else:
                        operation.status = OperationStatus.FAILED
                        operation.error = "Tagging returned False"
                        failed += 1

                except Exception as e:
                    operation.status = OperationStatus.FAILED
                    operation.error = str(e)
                    failed += 1
                    logger.error(f"Failed to tag {operation.resource_id}: {e}")

                    if fail_fast:
                        logger.warning("Fail-fast enabled, stopping on first error")
                        break

            # If fail_fast triggered, break outer loop
            if fail_fast and any(op.status == OperationStatus.FAILED for op in batch):
                break

        # Rollback if enabled and there are failures
        if self.enable_rollback and failed > 0:
            logger.warning(f"Rolling back {successful} successful operations due to {failed} failures")
            rolled_back = self._rollback_operations(operations)

        duration = time.time() - start_time

        result = BulkTaggingResult(
            total_operations=len(operations),
            successful=successful if not self.enable_rollback or failed == 0 else 0,
            failed=failed,
            rolled_back=rolled_back,
            operations=operations,
            duration_seconds=duration,
            dry_run=False
        )

        logger.info(
            f"Bulk tagging complete: {successful} successful, {failed} failed, "
            f"{rolled_back} rolled back ({duration:.2f}s)"
        )

        return result

    def _rollback_operations(
        self,
        operations: List[TagOperation]
    ) -> int:
        """Rollback successful operations by restoring original tags.

        Args:
            operations: List of operations to rollback

        Returns:
            Number of operations rolled back
        """
        rolled_back = 0

        # Only rollback completed operations
        completed_ops = [
            op for op in operations
            if op.status == OperationStatus.COMPLETED
        ]

        logger.info(f"Rolling back {len(completed_ops)} operations")

        for operation in completed_ops:
            try:
                scanner = self._get_scanner(
                    operation.resource_type,
                    operation.region
                )

                # Restore original tags
                if operation.original_tags:
                    scanner.apply_tags(
                        operation.resource_id,
                        operation.original_tags
                    )
                else:
                    # If no original tags, we should remove the tags we added
                    # This is complex and may not be supported for all resources
                    logger.warning(
                        f"Cannot fully rollback {operation.resource_id} "
                        "(no original tags to restore)"
                    )

                operation.status = OperationStatus.ROLLED_BACK
                rolled_back += 1

            except Exception as e:
                logger.error(f"Failed to rollback {operation.resource_id}: {e}")

        return rolled_back

    def tag_by_filter(
        self,
        resource_type: ResourceType,
        region: str,
        filter_func: callable,
        tags: Dict[str, str],
        dry_run: bool = False
    ) -> BulkTaggingResult:
        """Tag resources matching a filter function.

        Args:
            resource_type: Type of resources to tag
            region: AWS region
            filter_func: Function that takes AWSResource and returns bool
            tags: Tags to apply
            dry_run: If True, don't actually apply tags

        Returns:
            BulkTaggingResult
        """
        # Get scanner
        scanner = self._get_scanner(resource_type, region)

        # Scan resources
        scan_result = scanner.scan()

        # Apply filter
        matching_resources = [
            resource for resource in scan_result.resources
            if filter_func(resource)
        ]

        logger.info(
            f"Found {len(matching_resources)} resources matching filter out of "
            f"{scan_result.total_resources} total"
        )

        # Bulk tag
        return self.bulk_tag_resources(
            resources=matching_resources,
            tags=tags,
            dry_run=dry_run
        )

    def tag_untagged_resources(
        self,
        resource_type: ResourceType,
        region: str,
        tags: Dict[str, str],
        dry_run: bool = False
    ) -> BulkTaggingResult:
        """Tag all untagged resources of a specific type.

        Args:
            resource_type: Type of resources to tag
            region: AWS region
            tags: Tags to apply
            dry_run: If True, don't actually apply tags

        Returns:
            BulkTaggingResult
        """
        return self.tag_by_filter(
            resource_type=resource_type,
            region=region,
            filter_func=lambda r: not r.is_tagged,
            tags=tags,
            dry_run=dry_run
        )

    def generate_tagging_script(
        self,
        operations: List[TagOperation],
        output_format: str = "bash"
    ) -> str:
        """Generate executable script for tagging operations.

        Args:
            operations: List of tagging operations
            output_format: Script format ('bash', 'python', 'terraform')

        Returns:
            Script content as string
        """
        if output_format == "bash":
            return self._generate_bash_script(operations)
        elif output_format == "python":
            return self._generate_python_script(operations)
        elif output_format == "terraform":
            return self._generate_terraform_script(operations)
        else:
            raise ValueError(f"Unsupported output format: {output_format}")

    def _generate_bash_script(self, operations: List[TagOperation]) -> str:
        """Generate bash script for AWS CLI."""
        script_lines = [
            "#!/bin/bash",
            "# Auto-generated bulk tagging script for AWS TagSense",
            "# WARNING: Review carefully before executing",
            "",
            "set -e  # Exit on error",
            ""
        ]

        for op in operations:
            # Build tag string
            tags_str = " ".join([
                f"Key={k},Value={v}"
                for k, v in op.tags_to_apply.items()
            ])

            if op.resource_type == ResourceType.EC2:
                script_lines.append(
                    f"aws ec2 create-tags --region {op.region} "
                    f"--resources {op.resource_id} "
                    f"--tags {tags_str}"
                )
            elif op.resource_type == ResourceType.LAMBDA:
                script_lines.append(
                    f"aws lambda tag-resource --region {op.region} "
                    f"--resource $(aws lambda get-function --region {op.region} "
                    f"--function-name {op.resource_id} --query 'Configuration.FunctionArn' --output text) "
                    f"--tags {json.dumps(op.tags_to_apply)}"
                )
            elif op.resource_type == ResourceType.S3:
                script_lines.append(
                    f"aws s3api put-bucket-tagging --bucket {op.resource_id} "
                    f"--tagging 'TagSet=[{','.join([f'{{Key={k},Value={v}}}' for k, v in op.tags_to_apply.items()])}]'"
                )
            # Add more resource types as needed

            script_lines.append("")

        script_lines.append("echo 'Tagging complete!'")
        return "\n".join(script_lines)

    def _generate_python_script(self, operations: List[TagOperation]) -> str:
        """Generate Python script using boto3."""
        script = f"""#!/usr/bin/env python3
\"\"\"Auto-generated bulk tagging script for AWS TagSense\"\"\"

import boto3
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    \"\"\"Execute bulk tagging operations.\"\"\"

    # Operations to perform
    operations = {json.dumps([
        {{
            'resource_id': op.resource_id,
            'resource_type': op.resource_type.value,
            'region': op.region,
            'tags': op.tags_to_apply
        }}
        for op in operations
    ], indent=8)}

    for op in operations:
        try:
            resource_type = op['resource_type']
            region = op['region']
            resource_id = op['resource_id']
            tags = op['tags']

            if resource_type == 'EC2':
                ec2 = boto3.client('ec2', region_name=region)
                ec2.create_tags(
                    Resources=[resource_id],
                    Tags=[{{'Key': k, 'Value': v}} for k, v in tags.items()]
                )
            elif resource_type == 'Lambda':
                lambda_client = boto3.client('lambda', region_name=region)
                response = lambda_client.get_function(FunctionName=resource_id)
                arn = response['Configuration']['FunctionArn']
                lambda_client.tag_resource(Resource=arn, Tags=tags)
            elif resource_type == 'S3':
                s3 = boto3.client('s3')
                s3.put_bucket_tagging(
                    Bucket=resource_id,
                    Tagging={{'TagSet': [{{'Key': k, 'Value': v}} for k, v in tags.items()]}}
                )

            logger.info(f"Tagged {{resource_id}} successfully")

        except Exception as e:
            logger.error(f"Failed to tag {{resource_id}}: {{e}}")

    logger.info("Tagging complete!")

if __name__ == '__main__':
    main()
"""
        return script

    def _generate_terraform_script(self, operations: List[TagOperation]) -> str:
        """Generate Terraform configuration for tagging."""
        # This is a simplified example
        tf_resources = []

        for i, op in enumerate(operations):
            if op.resource_type == ResourceType.EC2:
                tf_resources.append(f"""
resource "aws_ec2_tag" "tag_{i}" {{
  resource_id = "{op.resource_id}"

  {chr(10).join([f'  key   = "{k}"' + chr(10) + f'  value = "{v}"' for k, v in op.tags_to_apply.items()])}
}}
""")

        return "\n".join(tf_resources)
