"""
RDS Database Scanner for AWS TagSense.

Comprehensive scanner for RDS instances with:
- Encryption analysis
- Backup configuration
- Multi-AZ deployment
- Security group analysis
- Compliance checking
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


class RDSScanner(BaseResourceScanner):
    """Scanner for RDS database instances with security and compliance focus."""

    @property
    def client(self):
        """Get or create RDS client."""
        if self._client is None:
            self._client = self.session.client('rds')
        return self._client

    def get_resource_type(self) -> ResourceType:
        """Return RDS resource type."""
        return ResourceType.RDS

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    def scan(self, db_instance_identifier: Optional[str] = None) -> ScanResult:
        """Scan RDS database instances in the region.

        Args:
            db_instance_identifier: Optional specific DB instance to scan

        Returns:
            ScanResult containing all RDS instances with metadata

        Raises:
            ResourceScanError: If the scan fails
        """
        try:
            logger.info(f"Starting RDS database scan in region {self.region}")

            resources = []

            # Use paginator for large result sets
            paginator = self.client.get_paginator('describe_db_instances')
            page_iterator = paginator.paginate(
                DBInstanceIdentifier=db_instance_identifier
            ) if db_instance_identifier else paginator.paginate()

            for page in page_iterator:
                for db_instance in page.get('DBInstances', []):
                    try:
                        # Extract basic info
                        db_id = db_instance['DBInstanceIdentifier']
                        arn = db_instance['DBInstanceArn']

                        # Get tags
                        tags = self._get_db_instance_tags(arn)

                        # Get comprehensive metadata
                        metadata = self._extract_db_metadata(db_instance)

                        resource = AWSResource(
                            resource_id=db_id,
                            resource_type=ResourceType.RDS,
                            region=self.region,
                            tags=tags,
                            state=db_instance.get('DBInstanceStatus', 'unknown'),
                            metadata=metadata
                        )

                        resources.append(resource)

                        logger.debug(
                            f"Scanned RDS instance: {db_id} "
                            f"(engine={metadata.get('engine')}, "
                            f"encrypted={metadata.get('encrypted')}, "
                            f"multi_az={metadata.get('multi_az')})"
                        )

                    except ClientError as e:
                        logger.warning(f"Error scanning DB instance: {e}")
                        continue

            # Separate tagged vs untagged
            tagged = [r for r in resources if r.is_tagged]
            untagged = [r for r in resources if not r.is_tagged]

            scan_result = ScanResult(
                resource_type=ResourceType.RDS,
                region=self.region,
                total_resources=len(resources),
                tagged_resources=tagged,
                untagged_resources=untagged,
                resources=resources
            )

            logger.info(
                f"RDS scan complete in {self.region}: {len(resources)} instances, "
                f"{len(untagged)} untagged ({scan_result.tagging_compliance_rate:.1f}% compliant)"
            )

            return scan_result

        except ClientError as e:
            error_msg = f"AWS API error during RDS scan: {e.response['Error']['Message']}"
            logger.error(error_msg, exc_info=True)
            raise ResourceScanError(error_msg) from e

        except BotoCoreError as e:
            error_msg = f"Boto3 error during RDS scan: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise AWSError(error_msg) from e

        except Exception as e:
            error_msg = f"Unexpected error during RDS scan: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise ResourceScanError(error_msg) from e

    def _get_db_instance_tags(self, resource_arn: str) -> Dict[str, str]:
        """Get tags for a specific DB instance.

        Args:
            resource_arn: ARN of the RDS instance

        Returns:
            Dictionary of tag key-value pairs
        """
        try:
            response = self.client.list_tags_for_resource(ResourceName=resource_arn)
            tag_list = response.get('TagList', [])
            return {tag['Key']: tag['Value'] for tag in tag_list}

        except ClientError as e:
            logger.warning(f"Error getting tags for RDS instance {resource_arn}: {e}")
            return {}

    def _extract_db_metadata(self, db_instance: Dict[str, Any]) -> Dict[str, Any]:
        """Extract comprehensive metadata from DB instance description.

        Args:
            db_instance: DB instance description from AWS API

        Returns:
            Dictionary with structured metadata
        """
        metadata = {
            'db_instance_identifier': db_instance.get('DBInstanceIdentifier'),
            'db_instance_class': db_instance.get('DBInstanceClass'),
            'engine': db_instance.get('Engine'),
            'engine_version': db_instance.get('EngineVersion'),
            'status': db_instance.get('DBInstanceStatus'),
            'allocated_storage': db_instance.get('AllocatedStorage'),
            'storage_type': db_instance.get('StorageType'),
            'iops': db_instance.get('Iops'),
            'master_username': db_instance.get('MasterUsername'),
            'db_name': db_instance.get('DBName'),
            'endpoint': db_instance.get('Endpoint', {}).get('Address'),
            'port': db_instance.get('Endpoint', {}).get('Port'),
            'availability_zone': db_instance.get('AvailabilityZone'),
            'multi_az': db_instance.get('MultiAZ', False),
            'encrypted': db_instance.get('StorageEncrypted', False),
            'kms_key_id': db_instance.get('KmsKeyId'),
            'publicly_accessible': db_instance.get('PubliclyAccessible', False),
            'backup_retention_period': db_instance.get('BackupRetentionPeriod', 0),
            'preferred_backup_window': db_instance.get('PreferredBackupWindow'),
            'preferred_maintenance_window': db_instance.get('PreferredMaintenanceWindow'),
            'latest_restorable_time': str(db_instance.get('LatestRestorableTime')) if db_instance.get('LatestRestorableTime') else None,
            'auto_minor_version_upgrade': db_instance.get('AutoMinorVersionUpgrade', False),
            'deletion_protection': db_instance.get('DeletionProtection', False),
            'vpc_security_groups': [sg['VpcSecurityGroupId'] for sg in db_instance.get('VpcSecurityGroups', [])],
            'db_subnet_group': db_instance.get('DBSubnetGroup', {}).get('DBSubnetGroupName'),
            'parameter_group': db_instance.get('DBParameterGroups', [{}])[0].get('DBParameterGroupName') if db_instance.get('DBParameterGroups') else None,
            'option_group': db_instance.get('OptionGroupMemberships', [{}])[0].get('OptionGroupName') if db_instance.get('OptionGroupMemberships') else None,
            'performance_insights_enabled': db_instance.get('PerformanceInsightsEnabled', False),
            'monitoring_interval': db_instance.get('MonitoringInterval', 0),
            'enhanced_monitoring_arn': db_instance.get('EnhancedMonitoringResourceArn'),
            'iam_database_authentication': db_instance.get('IAMDatabaseAuthenticationEnabled', False),
            'instance_create_time': str(db_instance.get('InstanceCreateTime')) if db_instance.get('InstanceCreateTime') else None,
        }

        # Calculate security and compliance scores
        metadata['security_score'] = self._calculate_security_score(metadata)
        metadata['compliance_issues'] = self._identify_compliance_issues(metadata)

        return metadata

    def _calculate_security_score(self, metadata: Dict[str, Any]) -> int:
        """Calculate security score for a database instance.

        Args:
            metadata: Database instance metadata

        Returns:
            Security score from 0-100
        """
        score = 100

        # Encryption check (critical)
        if not metadata.get('encrypted'):
            score -= 40

        # Public accessibility (critical)
        if metadata.get('publicly_accessible'):
            score -= 30

        # Backup retention (important)
        if metadata.get('backup_retention_period', 0) < 7:
            score -= 15

        # Multi-AZ (important for production)
        if not metadata.get('multi_az'):
            score -= 10

        # Deletion protection
        if not metadata.get('deletion_protection'):
            score -= 5

        return max(score, 0)

    def _identify_compliance_issues(self, metadata: Dict[str, Any]) -> List[Dict[str, str]]:
        """Identify compliance issues with the database configuration.

        Args:
            metadata: Database instance metadata

        Returns:
            List of compliance issues with severity and recommendations
        """
        issues = []

        # Encryption (HIPAA, PCI-DSS requirement)
        if not metadata.get('encrypted'):
            issues.append({
                'severity': 'CRITICAL',
                'issue': 'Database is not encrypted at rest',
                'frameworks': ['HIPAA', 'PCI-DSS', 'SOC 2'],
                'recommendation': 'Enable encryption at rest using AWS KMS. Note: Requires snapshot and restore.'
            })

        # Public accessibility (security best practice)
        if metadata.get('publicly_accessible'):
            issues.append({
                'severity': 'HIGH',
                'issue': 'Database is publicly accessible',
                'frameworks': ['PCI-DSS', 'SOC 2', 'Security Best Practices'],
                'recommendation': 'Disable public accessibility and use VPN or Direct Connect for access'
            })

        # Backup retention (compliance requirement)
        backup_days = metadata.get('backup_retention_period', 0)
        if backup_days < 7:
            issues.append({
                'severity': 'HIGH',
                'issue': f'Backup retention period is only {backup_days} days (minimum 7 recommended)',
                'frameworks': ['SOC 2', 'HIPAA'],
                'recommendation': 'Set backup retention to at least 7 days for compliance'
            })

        # Multi-AZ (high availability requirement)
        if not metadata.get('multi_az') and metadata.get('status') == 'available':
            issues.append({
                'severity': 'MEDIUM',
                'issue': 'Multi-AZ deployment is not enabled',
                'frameworks': ['SOC 2'],
                'recommendation': 'Enable Multi-AZ for production databases to ensure high availability'
            })

        # Deletion protection (data protection)
        if not metadata.get('deletion_protection'):
            issues.append({
                'severity': 'MEDIUM',
                'issue': 'Deletion protection is not enabled',
                'frameworks': ['Security Best Practices'],
                'recommendation': 'Enable deletion protection to prevent accidental database deletion'
            })

        # Enhanced monitoring
        if metadata.get('monitoring_interval', 0) == 0:
            issues.append({
                'severity': 'LOW',
                'issue': 'Enhanced monitoring is not enabled',
                'frameworks': ['SOC 2'],
                'recommendation': 'Enable enhanced monitoring for better observability'
            })

        # Performance Insights
        if not metadata.get('performance_insights_enabled'):
            issues.append({
                'severity': 'LOW',
                'issue': 'Performance Insights is not enabled',
                'frameworks': [],
                'recommendation': 'Enable Performance Insights for query performance analysis'
            })

        return issues

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    def apply_tags(self, resource_id: str, tags: Dict[str, str]) -> bool:
        """Apply tags to an RDS instance.

        Args:
            resource_id: DB instance identifier
            tags: Dictionary of tags to apply

        Returns:
            True if successful

        Raises:
            AWSError: If tagging fails
        """
        try:
            # Get instance ARN
            response = self.client.describe_db_instances(DBInstanceIdentifier=resource_id)
            db_instances = response.get('DBInstances', [])

            if not db_instances:
                raise AWSError(f"RDS instance {resource_id} not found")

            arn = db_instances[0]['DBInstanceArn']

            # Get existing tags
            existing_tags = self._get_db_instance_tags(arn)

            # Merge with new tags
            merged_tags = {**existing_tags, **tags}

            # Convert to AWS tag format
            tag_list = [{'Key': k, 'Value': v} for k, v in merged_tags.items()]

            # Remove all existing tags first (RDS doesn't support merging)
            if existing_tags:
                self.client.remove_tags_from_resource(
                    ResourceName=arn,
                    TagKeys=list(existing_tags.keys())
                )

            # Apply all tags
            self.client.add_tags_to_resource(
                ResourceName=arn,
                Tags=tag_list
            )

            logger.info(
                f"Successfully applied {len(tags)} tags to RDS instance {resource_id}",
                extra={"db_instance": resource_id, "tag_count": len(tags)}
            )

            return True

        except ClientError as e:
            error_msg = f"Failed to apply tags to RDS instance {resource_id}: {e}"
            logger.error(error_msg, exc_info=True)
            raise AWSError(error_msg) from e

    def generate_compliance_report(self, db_instance_identifier: str) -> Dict[str, Any]:
        """Generate comprehensive compliance report for a database instance.

        Args:
            db_instance_identifier: DB instance identifier

        Returns:
            Dictionary with compliance report
        """
        try:
            # Get instance details
            response = self.client.describe_db_instances(
                DBInstanceIdentifier=db_instance_identifier
            )
            db_instance = response['DBInstances'][0]
            metadata = self._extract_db_metadata(db_instance)

            return {
                'db_instance_identifier': db_instance_identifier,
                'engine': metadata['engine'],
                'security_score': metadata['security_score'],
                'risk_level': 'CRITICAL' if metadata['security_score'] < 50 else 'HIGH' if metadata['security_score'] < 70 else 'MEDIUM' if metadata['security_score'] < 90 else 'LOW',
                'compliance_issues': metadata['compliance_issues'],
                'configuration_summary': {
                    'encrypted': metadata['encrypted'],
                    'publicly_accessible': metadata['publicly_accessible'],
                    'multi_az': metadata['multi_az'],
                    'backup_retention_days': metadata['backup_retention_period'],
                    'deletion_protection': metadata['deletion_protection'],
                    'enhanced_monitoring': metadata['monitoring_interval'] > 0,
                    'performance_insights': metadata['performance_insights_enabled']
                },
                'recommendations': [issue['recommendation'] for issue in metadata['compliance_issues']]
            }

        except Exception as e:
            logger.error(f"Error generating compliance report for {db_instance_identifier}: {e}")
            raise
