"""
EBS Volume Scanner for AWS TagSense.

Comprehensive scanner for EBS volumes with:
- Snapshot analysis
- Encryption status
- Volume type and performance
- Cost optimization opportunities
- Orphaned volume detection
"""

import logging
from typing import Dict, List, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential
from botocore.exceptions import ClientError, BotoCoreError
from datetime import datetime, timezone

from .resource_scanner import (
    BaseResourceScanner,
    ResourceType,
    AWSResource,
    ScanResult
)
from .exceptions import AWSError, ResourceScanError

logger = logging.getLogger(__name__)


class EBSScanner(BaseResourceScanner):
    """Scanner for EBS volumes with cost optimization focus."""

    @property
    def client(self):
        """Get or create EC2 client (EBS is part of EC2 service)."""
        if self._client is None:
            self._client = self.session.client('ec2')
        return self._client

    def get_resource_type(self) -> ResourceType:
        """Return EBS resource type."""
        return ResourceType.EBS

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    def scan(self, volume_ids: Optional[List[str]] = None) -> ScanResult:
        """Scan EBS volumes in the region.

        Args:
            volume_ids: Optional list of specific volume IDs to scan

        Returns:
            ScanResult containing all EBS volumes with metadata

        Raises:
            ResourceScanError: If the scan fails
        """
        try:
            logger.info(f"Starting EBS volume scan in region {self.region}")

            # Build filters
            filters = []
            if volume_ids:
                filters.append({'Name': 'volume-id', 'Values': volume_ids})

            resources = []

            # Use paginator for large result sets
            paginator = self.client.get_paginator('describe_volumes')
            page_iterator = paginator.paginate(
                VolumeIds=volume_ids if volume_ids else [],
                Filters=filters if filters else []
            ) if volume_ids else paginator.paginate()

            for page in page_iterator:
                for volume in page.get('Volumes', []):
                    try:
                        volume_id = volume['VolumeId']

                        # Get tags
                        tags = {tag['Key']: tag['Value'] for tag in volume.get('Tags', [])}

                        # Get comprehensive metadata
                        metadata = self._extract_volume_metadata(volume)

                        resource = AWSResource(
                            resource_id=volume_id,
                            resource_type=ResourceType.EBS,
                            region=self.region,
                            tags=tags,
                            state=volume.get('State', 'unknown'),
                            metadata=metadata
                        )

                        resources.append(resource)

                        logger.debug(
                            f"Scanned EBS volume: {volume_id} "
                            f"(type={metadata.get('volume_type')}, "
                            f"size={metadata.get('size')}GB, "
                            f"attached={metadata.get('is_attached')})"
                        )

                    except Exception as e:
                        logger.warning(f"Error scanning volume: {e}")
                        continue

            # Separate tagged vs untagged
            tagged = [r for r in resources if r.is_tagged]
            untagged = [r for r in resources if not r.is_tagged]

            scan_result = ScanResult(
                resource_type=ResourceType.EBS,
                region=self.region,
                total_resources=len(resources),
                tagged_resources=tagged,
                untagged_resources=untagged,
                resources=resources
            )

            logger.info(
                f"EBS scan complete in {self.region}: {len(resources)} volumes, "
                f"{len(untagged)} untagged ({scan_result.tagging_compliance_rate:.1f}% compliant)"
            )

            return scan_result

        except ClientError as e:
            error_msg = f"AWS API error during EBS scan: {e.response['Error']['Message']}"
            logger.error(error_msg, exc_info=True)
            raise ResourceScanError(error_msg) from e

        except BotoCoreError as e:
            error_msg = f"Boto3 error during EBS scan: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise AWSError(error_msg) from e

        except Exception as e:
            error_msg = f"Unexpected error during EBS scan: {str(e)}"
            logger.error(error_msg, exc_info=True)
            raise ResourceScanError(error_msg) from e

    def _extract_volume_metadata(self, volume: Dict[str, Any]) -> Dict[str, Any]:
        """Extract comprehensive metadata from volume description.

        Args:
            volume: Volume description from AWS API

        Returns:
            Dictionary with structured metadata
        """
        # Calculate age
        create_time = volume.get('CreateTime')
        age_days = (datetime.now(timezone.utc) - create_time).days if create_time else 0

        # Determine attachment status
        attachments = volume.get('Attachments', [])
        is_attached = len(attachments) > 0
        instance_id = attachments[0].get('InstanceId') if attachments else None

        metadata = {
            'volume_id': volume.get('VolumeId'),
            'volume_type': volume.get('VolumeType'),
            'size': volume.get('Size'),  # GB
            'iops': volume.get('Iops'),
            'throughput': volume.get('Throughput'),
            'state': volume.get('State'),
            'encrypted': volume.get('Encrypted', False),
            'kms_key_id': volume.get('KmsKeyId'),
            'snapshot_id': volume.get('SnapshotId'),
            'availability_zone': volume.get('AvailabilityZone'),
            'create_time': str(create_time) if create_time else None,
            'age_days': age_days,
            'is_attached': is_attached,
            'instance_id': instance_id,
            'device_name': attachments[0].get('Device') if attachments else None,
            'delete_on_termination': attachments[0].get('DeleteOnTermination', False) if attachments else False,
            'multi_attach_enabled': volume.get('MultiAttachEnabled', False),
        }

        # Add cost estimation
        metadata['estimated_monthly_cost'] = self._estimate_monthly_cost(
            volume_type=metadata['volume_type'],
            size_gb=metadata['size'],
            iops=metadata.get('iops'),
            throughput=metadata.get('throughput')
        )

        # Identify optimization opportunities
        metadata['optimization_opportunities'] = self._identify_optimization_opportunities(metadata)

        # Calculate value score (cost vs utilization)
        metadata['value_score'] = self._calculate_value_score(metadata)

        return metadata

    def _estimate_monthly_cost(
        self,
        volume_type: str,
        size_gb: int,
        iops: Optional[int] = None,
        throughput: Optional[int] = None
    ) -> float:
        """Estimate monthly cost for an EBS volume.

        Args:
            volume_type: EBS volume type (gp3, gp2, io2, etc.)
            size_gb: Volume size in GB
            iops: Provisioned IOPS (for io1/io2)
            throughput: Provisioned throughput (for gp3)

        Returns:
            Estimated monthly cost in USD
        """
        # Pricing as of 2024 (approximate, varies by region)
        pricing = {
            'gp3': 0.08,  # per GB-month
            'gp2': 0.10,  # per GB-month
            'io1': 0.125,  # per GB-month
            'io2': 0.125,  # per GB-month
            'st1': 0.045,  # per GB-month
            'sc1': 0.015,  # per GB-month
            'standard': 0.05,  # per GB-month (magnetic)
        }

        base_cost = pricing.get(volume_type, 0.10) * size_gb

        # Add IOPS cost for provisioned IOPS volumes
        if volume_type in ['io1', 'io2'] and iops:
            base_iops = 3000 if volume_type == 'gp3' else 0
            if iops > base_iops:
                iops_cost = (iops - base_iops) * 0.005  # $0.005 per IOPS-month
                base_cost += iops_cost

        # Add throughput cost for gp3
        if volume_type == 'gp3' and throughput and throughput > 125:
            throughput_cost = (throughput - 125) * 0.04  # $0.04 per MB/s-month
            base_cost += throughput_cost

        return round(base_cost, 2)

    def _identify_optimization_opportunities(self, metadata: Dict[str, Any]) -> List[Dict[str, str]]:
        """Identify cost optimization opportunities for a volume.

        Args:
            metadata: Volume metadata

        Returns:
            List of optimization opportunities
        """
        opportunities = []

        # Unattached volumes (orphaned)
        if not metadata['is_attached']:
            opportunities.append({
                'type': 'ORPHANED_VOLUME',
                'severity': 'HIGH',
                'opportunity': f'Volume is unattached and costing ${metadata["estimated_monthly_cost"]}/month',
                'recommendation': 'Delete if no longer needed or create snapshot and delete volume',
                'potential_savings': f'${metadata["estimated_monthly_cost"]}/month'
            })

        # Old gp2 volumes should migrate to gp3
        if metadata['volume_type'] == 'gp2' and metadata['size'] > 20:
            old_cost = metadata['estimated_monthly_cost']
            new_cost = self._estimate_monthly_cost('gp3', metadata['size'])
            savings = old_cost - new_cost

            if savings > 0:
                opportunities.append({
                    'type': 'VOLUME_TYPE_UPGRADE',
                    'severity': 'MEDIUM',
                    'opportunity': 'Migrate from gp2 to gp3 for better price/performance',
                    'recommendation': 'Modify volume type to gp3 (can be done online)',
                    'potential_savings': f'${savings:.2f}/month ({(savings/old_cost*100):.1f}% reduction)'
                })

        # Over-provisioned IOPS
        if metadata['volume_type'] in ['io1', 'io2'] and metadata.get('iops', 0) > 16000:
            opportunities.append({
                'type': 'OVER_PROVISIONED_IOPS',
                'severity': 'MEDIUM',
                'opportunity': f'Very high IOPS provisioned ({metadata["iops"]})',
                'recommendation': 'Review CloudWatch metrics to determine if IOPS can be reduced',
                'potential_savings': 'Review required'
            })

        # Encryption not enabled (security + compliance)
        if not metadata['encrypted']:
            opportunities.append({
                'type': 'ENCRYPTION_DISABLED',
                'severity': 'HIGH',
                'opportunity': 'Volume is not encrypted (compliance risk)',
                'recommendation': 'Create encrypted snapshot, restore to new encrypted volume',
                'potential_savings': 'Compliance risk mitigation'
            })

        # Old volumes (potential for snapshot + deletion)
        if metadata['age_days'] > 365 and not metadata['is_attached']:
            opportunities.append({
                'type': 'OLD_UNATTACHED_VOLUME',
                'severity': 'HIGH',
                'opportunity': f'Volume is {metadata["age_days"]} days old and unattached',
                'recommendation': 'Snapshot and delete to reduce storage costs by 95%',
                'potential_savings': f'${metadata["estimated_monthly_cost"] * 0.95:.2f}/month'
            })

        return opportunities

    def _calculate_value_score(self, metadata: Dict[str, Any]) -> int:
        """Calculate value score for a volume (0-100, higher is better).

        Args:
            metadata: Volume metadata

        Returns:
            Value score
        """
        score = 100

        # Penalize unattached volumes
        if not metadata['is_attached']:
            score -= 50

        # Penalize lack of encryption
        if not metadata['encrypted']:
            score -= 20

        # Penalize old unattached volumes
        if metadata['age_days'] > 365 and not metadata['is_attached']:
            score -= 20

        # Penalize gp2 over gp3
        if metadata['volume_type'] == 'gp2':
            score -= 10

        return max(score, 0)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    def apply_tags(self, resource_id: str, tags: Dict[str, str]) -> bool:
        """Apply tags to an EBS volume.

        Args:
            resource_id: Volume ID
            tags: Dictionary of tags to apply

        Returns:
            True if successful

        Raises:
            AWSError: If tagging fails
        """
        try:
            # Convert to AWS tag format
            tag_list = [{'Key': k, 'Value': v} for k, v in tags.items()]

            # Apply tags
            self.client.create_tags(
                Resources=[resource_id],
                Tags=tag_list
            )

            logger.info(
                f"Successfully applied {len(tags)} tags to EBS volume {resource_id}",
                extra={"volume_id": resource_id, "tag_count": len(tags)}
            )

            return True

        except ClientError as e:
            error_msg = f"Failed to apply tags to EBS volume {resource_id}: {e}"
            logger.error(error_msg, exc_info=True)
            raise AWSError(error_msg) from e

    def find_orphaned_volumes(self) -> List[AWSResource]:
        """Find all unattached (orphaned) EBS volumes.

        Returns:
            List of unattached volumes
        """
        scan_result = self.scan()
        orphaned = [
            resource for resource in scan_result.resources
            if not resource.metadata.get('is_attached')
        ]

        logger.info(f"Found {len(orphaned)} orphaned volumes in {self.region}")
        return orphaned

    def calculate_potential_savings(self) -> Dict[str, Any]:
        """Calculate potential cost savings from optimization opportunities.

        Returns:
            Dictionary with savings analysis
        """
        scan_result = self.scan()

        total_monthly_cost = 0.0
        potential_savings = 0.0
        opportunities_by_type = {}

        for resource in scan_result.resources:
            monthly_cost = resource.metadata.get('estimated_monthly_cost', 0)
            total_monthly_cost += monthly_cost

            for opp in resource.metadata.get('optimization_opportunities', []):
                opp_type = opp['type']
                if opp_type not in opportunities_by_type:
                    opportunities_by_type[opp_type] = {
                        'count': 0,
                        'total_savings': 0.0,
                        'resources': []
                    }

                opportunities_by_type[opp_type]['count'] += 1
                opportunities_by_type[opp_type]['resources'].append(resource.resource_id)

                # Parse savings (this is simplified)
                if 'potential_savings' in opp:
                    savings_str = opp['potential_savings']
                    if savings_str.startswith('$') and '/month' in savings_str:
                        try:
                            savings = float(savings_str.split('$')[1].split('/')[0])
                            potential_savings += savings
                            opportunities_by_type[opp_type]['total_savings'] += savings
                        except:
                            pass

        return {
            'total_volumes': scan_result.total_resources,
            'total_monthly_cost': round(total_monthly_cost, 2),
            'potential_monthly_savings': round(potential_savings, 2),
            'savings_percentage': round((potential_savings / total_monthly_cost * 100), 1) if total_monthly_cost > 0 else 0,
            'opportunities_by_type': opportunities_by_type,
            'top_recommendations': [
                'Snapshot and delete orphaned volumes',
                'Migrate gp2 volumes to gp3',
                'Enable encryption on unencrypted volumes',
                'Review and reduce over-provisioned IOPS'
            ]
        }
