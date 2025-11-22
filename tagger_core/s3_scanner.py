"""
S3 Bucket Scanner for AWS TagSense.

Comprehensive scanner for S3 buckets with:
- Encryption status analysis
- Versioning configuration
- Public access detection
- Lifecycle policies
- Cost optimization insights
"""

import logging
from typing import Dict, List, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential
from botocore.exceptions import ClientError, BotoCoreError

from .resource_scanner import (
    BaseResourceScanner,
    ResourceType,
    AWSResource,
    ScanResult
)
from .exceptions import AWSError, ResourceScanError

logger = logging.getLogger(__name__)


class S3Scanner(BaseResourceScanner):
    """Scanner for S3 buckets with comprehensive security and cost analysis."""

    @property
    def client(self):
        """Get or create S3 client."""
        if self._client is None:
            self._client = self.session.client('s3')
        return self._client

    def get_resource_type(self) -> ResourceType:
        """Return S3 resource type."""
        return ResourceType.S3

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    def scan(self) -> ScanResult:
        """Scan all S3 buckets in the account.

        Returns:
            ScanResult containing all S3 buckets with metadata

        Raises:
            ResourceScanError: If the scan fails
        """
        try:
            logger.info(f"Starting S3 bucket scan (region-independent)")

            # List all buckets (S3 is global, but we track region)
            response = self.client.list_buckets()
            buckets = response.get('Buckets', [])

            resources = []
            for bucket_info in buckets:
                bucket_name = bucket_info['Name']

                try:
                    # Get bucket location
                    location_response = self.client.get_bucket_location(Bucket=bucket_name)
                    bucket_region = location_response.get('LocationConstraint') or 'us-east-1'

                    # Get bucket tags
                    tags = self._get_bucket_tags(bucket_name)

                    # Get comprehensive bucket metadata
                    metadata = self._get_bucket_metadata(bucket_name, bucket_region)

                    resource = AWSResource(
                        resource_id=bucket_name,
                        resource_type=ResourceType.S3,
                        region=bucket_region,
                        tags=tags,
                        state='active',  # S3 buckets are always active
                        metadata=metadata
                    )

                    resources.append(resource)

                    logger.debug(
                        f"Scanned S3 bucket: {bucket_name} (region={bucket_region}, "
                        f"tags={len(tags)}, encrypted={metadata.get('encryption_enabled')})"
                    )

                except ClientError as e:
                    error_code = e.response['Error']['Code']
                    if error_code in ['NoSuchBucket', 'AccessDenied']:
                        logger.warning(f"Cannot access bucket {bucket_name}: {error_code}")
                        continue
                    else:
                        raise

            # Separate tagged vs untagged
            tagged = [r for r in resources if r.is_tagged]
            untagged = [r for r in resources if not r.is_tagged]

            scan_result = ScanResult(
                resource_type=ResourceType.S3,
                region='global',  # S3 is global service
                total_resources=len(resources),
                tagged_resources=tagged,
                untagged_resources=untagged,
                resources=resources
            )

            logger.info(
                f"S3 scan complete: {len(resources)} buckets total, "
                f"{len(untagged)} untagged ({scan_result.tagging_compliance_rate:.1f}% compliant)"
            )

            return scan_result

        except ClientError as e:
            error_msg = f"AWS API error during S3 scan: {e.response['Error']['Message']}"
            logger.error(error_msg, exc_info=True)
            raise ResourceScanError(error_msg) from e

        except BotoCoreError as e:
            error_msg = f"Boto3 error during S3 scan: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise AWSError(error_msg) from e

        except Exception as e:
            error_msg = f"Unexpected error during S3 scan: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise ResourceScanError(error_msg) from e

    def _get_bucket_tags(self, bucket_name: str) -> Dict[str, str]:
        """Get tags for a specific bucket.

        Args:
            bucket_name: Name of the S3 bucket

        Returns:
            Dictionary of tag key-value pairs
        """
        try:
            response = self.client.get_bucket_tagging(Bucket=bucket_name)
            tag_set = response.get('TagSet', [])
            return {tag['Key']: tag['Value'] for tag in tag_set}

        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchTagSet':
                return {}
            else:
                logger.warning(f"Error getting tags for bucket {bucket_name}: {e}")
                return {}

    def _get_bucket_metadata(self, bucket_name: str, region: str) -> Dict[str, Any]:
        """Get comprehensive metadata for a bucket.

        Args:
            bucket_name: Name of the S3 bucket
            region: AWS region of the bucket

        Returns:
            Dictionary with bucket metadata
        """
        metadata = {
            'bucket_name': bucket_name,
            'region': region,
            'creation_date': None,
            'encryption_enabled': False,
            'encryption_type': None,
            'versioning_enabled': False,
            'versioning_status': None,
            'public_access_block': {},
            'lifecycle_rules': 0,
            'replication_enabled': False,
            'logging_enabled': False,
            'intelligent_tiering': False
        }

        # Encryption status
        try:
            encryption = self.client.get_bucket_encryption(Bucket=bucket_name)
            if 'Rules' in encryption.get('ServerSideEncryptionConfiguration', {}):
                metadata['encryption_enabled'] = True
                rule = encryption['ServerSideEncryptionConfiguration']['Rules'][0]
                metadata['encryption_type'] = rule['ApplyServerSideEncryptionByDefault']['SSEAlgorithm']
        except ClientError as e:
            if e.response['Error']['Code'] != 'ServerSideEncryptionConfigurationNotFoundError':
                logger.debug(f"Error checking encryption for {bucket_name}: {e}")

        # Versioning status
        try:
            versioning = self.client.get_bucket_versioning(Bucket=bucket_name)
            status = versioning.get('Status', 'Disabled')
            metadata['versioning_status'] = status
            metadata['versioning_enabled'] = status == 'Enabled'
        except ClientError as e:
            logger.debug(f"Error checking versioning for {bucket_name}: {e}")

        # Public access block configuration
        try:
            public_access = self.client.get_public_access_block(Bucket=bucket_name)
            metadata['public_access_block'] = public_access.get('PublicAccessBlockConfiguration', {})
        except ClientError as e:
            if e.response['Error']['Code'] != 'NoSuchPublicAccessBlockConfiguration':
                logger.debug(f"Error checking public access for {bucket_name}: {e}")

        # Lifecycle rules
        try:
            lifecycle = self.client.get_bucket_lifecycle_configuration(Bucket=bucket_name)
            rules = lifecycle.get('Rules', [])
            metadata['lifecycle_rules'] = len(rules)
            # Check for intelligent tiering
            for rule in rules:
                if any(t.get('StorageClass') == 'INTELLIGENT_TIERING'
                       for t in rule.get('Transitions', [])):
                    metadata['intelligent_tiering'] = True
                    break
        except ClientError as e:
            if e.response['Error']['Code'] != 'NoSuchLifecycleConfiguration':
                logger.debug(f"Error checking lifecycle for {bucket_name}: {e}")

        # Replication
        try:
            replication = self.client.get_bucket_replication(Bucket=bucket_name)
            if 'ReplicationConfiguration' in replication:
                metadata['replication_enabled'] = True
        except ClientError as e:
            if e.response['Error']['Code'] != 'ReplicationConfigurationNotFoundError':
                logger.debug(f"Error checking replication for {bucket_name}: {e}")

        # Logging
        try:
            logging_config = self.client.get_bucket_logging(Bucket=bucket_name)
            metadata['logging_enabled'] = 'LoggingEnabled' in logging_config
        except ClientError as e:
            logger.debug(f"Error checking logging for {bucket_name}: {e}")

        return metadata

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    def apply_tags(self, resource_id: str, tags: Dict[str, str]) -> bool:
        """Apply tags to an S3 bucket.

        Args:
            resource_id: S3 bucket name
            tags: Dictionary of tags to apply

        Returns:
            True if successful

        Raises:
            AWSError: If tagging fails
        """
        try:
            # Get existing tags
            existing_tags = self._get_bucket_tags(resource_id)

            # Merge with new tags (new tags override existing)
            merged_tags = {**existing_tags, **tags}

            # Convert to AWS tag format
            tag_set = [{'Key': k, 'Value': v} for k, v in merged_tags.items()]

            # Apply tags
            self.client.put_bucket_tagging(
                Bucket=resource_id,
                Tagging={'TagSet': tag_set}
            )

            logger.info(
                f"Successfully applied {len(tags)} tags to S3 bucket {resource_id}",
                extra={"bucket": resource_id, "tag_count": len(tags)}
            )

            return True

        except ClientError as e:
            error_msg = f"Failed to apply tags to S3 bucket {resource_id}: {e}"
            logger.error(error_msg, exc_info=True)
            raise AWSError(error_msg) from e

    def get_bucket_size_estimate(self, bucket_name: str) -> Optional[int]:
        """Estimate bucket size using CloudWatch metrics.

        Args:
            bucket_name: Name of the S3 bucket

        Returns:
            Estimated size in bytes, or None if unavailable
        """
        try:
            cloudwatch = self.session.client('cloudwatch')
            from datetime import datetime, timedelta

            # Get bucket size from CloudWatch (StandardStorage metric)
            response = cloudwatch.get_metric_statistics(
                Namespace='AWS/S3',
                MetricName='BucketSizeBytes',
                Dimensions=[
                    {'Name': 'BucketName', 'Value': bucket_name},
                    {'Name': 'StorageType', 'Value': 'StandardStorage'}
                ],
                StartTime=datetime.utcnow() - timedelta(days=1),
                EndTime=datetime.utcnow(),
                Period=86400,  # 1 day
                Statistics=['Average']
            )

            datapoints = response.get('Datapoints', [])
            if datapoints:
                return int(datapoints[0]['Average'])

            return None

        except Exception as e:
            logger.debug(f"Could not estimate size for bucket {bucket_name}: {e}")
            return None

    def analyze_security_posture(self, bucket_name: str) -> Dict[str, Any]:
        """Analyze security configuration of a bucket.

        Args:
            bucket_name: Name of the S3 bucket

        Returns:
            Dictionary with security analysis
        """
        metadata = self._get_bucket_metadata(bucket_name, self.region)

        security_score = 100
        findings = []

        # Check encryption
        if not metadata['encryption_enabled']:
            security_score -= 30
            findings.append({
                'severity': 'HIGH',
                'finding': 'Bucket encryption is not enabled',
                'recommendation': 'Enable default encryption with AES256 or AWS KMS'
            })

        # Check versioning
        if not metadata['versioning_enabled']:
            security_score -= 20
            findings.append({
                'severity': 'MEDIUM',
                'finding': 'Versioning is not enabled',
                'recommendation': 'Enable versioning for data protection and recovery'
            })

        # Check public access block
        pab = metadata.get('public_access_block', {})
        if not all([
            pab.get('BlockPublicAcls'),
            pab.get('BlockPublicPolicy'),
            pab.get('IgnorePublicAcls'),
            pab.get('RestrictPublicBuckets')
        ]):
            security_score -= 40
            findings.append({
                'severity': 'CRITICAL',
                'finding': 'Public access block is not fully configured',
                'recommendation': 'Enable all public access block settings to prevent data leaks'
            })

        # Check logging
        if not metadata['logging_enabled']:
            security_score -= 10
            findings.append({
                'severity': 'LOW',
                'finding': 'Access logging is not enabled',
                'recommendation': 'Enable access logging for audit and compliance'
            })

        return {
            'bucket_name': bucket_name,
            'security_score': max(security_score, 0),
            'risk_level': 'CRITICAL' if security_score < 50 else 'HIGH' if security_score < 70 else 'MEDIUM' if security_score < 90 else 'LOW',
            'findings': findings,
            'compliant_features': {
                'encryption': metadata['encryption_enabled'],
                'versioning': metadata['versioning_enabled'],
                'public_access_blocked': len(findings) == 0 or not any('public' in f['finding'].lower() for f in findings),
                'logging': metadata['logging_enabled']
            }
        }
