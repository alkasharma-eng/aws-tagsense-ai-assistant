"""
Multi-Region Parallel Scanner for AWS TagSense.

Enterprise-grade parallel scanning across all AWS regions with:
- Concurrent async scanning
- Progress tracking
- Error resilience
- Aggregated results
"""

import asyncio
import logging
from typing import Dict, List, Any, Optional, Type
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor
import time

from .resource_scanner import BaseResourceScanner, ResourceType, ScanResult
from .ec2_scanner import EC2Scanner
from .lambda_scanner import LambdaScanner
from .s3_scanner import S3Scanner
from .rds_scanner import RDSScanner
from .ebs_scanner import EBSScanner

logger = logging.getLogger(__name__)


@dataclass
class RegionScanResult:
    """Result from scanning a single region."""
    region: str
    resource_type: ResourceType
    scan_result: Optional[ScanResult]
    success: bool
    error: Optional[str] = None
    scan_duration_seconds: float = 0.0


@dataclass
class MultiRegionScanResult:
    """Aggregated results from multi-region scan."""
    resource_type: ResourceType
    regions_scanned: List[str]
    successful_scans: int
    failed_scans: int
    total_resources: int
    total_untagged: int
    total_tagged: int
    tagging_compliance_rate: float
    region_results: List[RegionScanResult] = field(default_factory=list)
    total_scan_duration_seconds: float = 0.0

    def get_all_untagged_resources(self):
        """Get all untagged resources across all regions."""
        all_untagged = []
        for region_result in self.region_results:
            if region_result.success and region_result.scan_result:
                all_untagged.extend(region_result.scan_result.untagged_resources)
        return all_untagged

    def get_region_summary(self) -> Dict[str, Any]:
        """Get summary statistics by region."""
        summary = {}
        for region_result in self.region_results:
            if region_result.success and region_result.scan_result:
                summary[region_result.region] = {
                    'total': region_result.scan_result.total_resources,
                    'untagged': len(region_result.scan_result.untagged_resources),
                    'tagged': len(region_result.scan_result.tagged_resources),
                    'compliance_rate': region_result.scan_result.tagging_compliance_rate,
                    'scan_duration': region_result.scan_duration_seconds
                }
            else:
                summary[region_result.region] = {
                    'total': 0,
                    'error': region_result.error
                }
        return summary


class MultiRegionScanner:
    """Scan AWS resources across multiple regions in parallel."""

    # All AWS regions (as of 2024)
    ALL_REGIONS = [
        'us-east-1', 'us-east-2', 'us-west-1', 'us-west-2',
        'eu-west-1', 'eu-west-2', 'eu-west-3', 'eu-central-1', 'eu-north-1',
        'ap-south-1', 'ap-northeast-1', 'ap-northeast-2', 'ap-northeast-3',
        'ap-southeast-1', 'ap-southeast-2',
        'ca-central-1',
        'sa-east-1',
        'af-south-1',
        'me-south-1'
    ]

    # Commonly used regions
    COMMON_REGIONS = [
        'us-east-1', 'us-west-2',
        'eu-west-1', 'eu-central-1',
        'ap-northeast-1', 'ap-southeast-1'
    ]

    def __init__(
        self,
        profile: Optional[str] = None,
        max_workers: int = 10
    ):
        """Initialize multi-region scanner.

        Args:
            profile: AWS profile name
            max_workers: Maximum number of concurrent workers
        """
        self.profile = profile
        self.max_workers = max_workers

    def _get_scanner_class(self, resource_type: ResourceType) -> Type[BaseResourceScanner]:
        """Get scanner class for resource type.

        Args:
            resource_type: Type of resource to scan

        Returns:
            Scanner class
        """
        scanner_map = {
            ResourceType.EC2: EC2Scanner,
            ResourceType.LAMBDA: LambdaScanner,
            ResourceType.S3: S3Scanner,
            ResourceType.RDS: RDSScanner,
            ResourceType.EBS: EBSScanner
        }

        if resource_type not in scanner_map:
            raise ValueError(f"Unsupported resource type: {resource_type}")

        return scanner_map[resource_type]

    def _scan_region(
        self,
        region: str,
        resource_type: ResourceType
    ) -> RegionScanResult:
        """Scan a single region (thread-safe).

        Args:
            region: AWS region to scan
            resource_type: Type of resource to scan

        Returns:
            RegionScanResult
        """
        start_time = time.time()

        try:
            logger.info(f"Starting {resource_type.value} scan in {region}")

            # Get appropriate scanner
            scanner_class = self._get_scanner_class(resource_type)
            scanner = scanner_class(region=region, profile=self.profile)

            # Perform scan
            scan_result = scanner.scan()

            duration = time.time() - start_time

            logger.info(
                f"Completed {resource_type.value} scan in {region}: "
                f"{scan_result.total_resources} resources, "
                f"{len(scan_result.untagged_resources)} untagged "
                f"({duration:.2f}s)"
            )

            return RegionScanResult(
                region=region,
                resource_type=resource_type,
                scan_result=scan_result,
                success=True,
                scan_duration_seconds=duration
            )

        except Exception as e:
            duration = time.time() - start_time
            error_msg = f"Failed to scan {region}: {str(e)}"
            logger.error(error_msg, exc_info=True)

            return RegionScanResult(
                region=region,
                resource_type=resource_type,
                scan_result=None,
                success=False,
                error=error_msg,
                scan_duration_seconds=duration
            )

    def scan_all_regions(
        self,
        resource_type: ResourceType,
        regions: Optional[List[str]] = None,
        use_all_regions: bool = False
    ) -> MultiRegionScanResult:
        """Scan resources across multiple regions in parallel.

        Args:
            resource_type: Type of resource to scan
            regions: List of regions to scan (default: COMMON_REGIONS)
            use_all_regions: If True, scan ALL AWS regions

        Returns:
            MultiRegionScanResult with aggregated data
        """
        start_time = time.time()

        # Determine regions to scan
        if use_all_regions:
            target_regions = self.ALL_REGIONS
        elif regions:
            target_regions = regions
        else:
            target_regions = self.COMMON_REGIONS

        logger.info(
            f"Starting multi-region scan for {resource_type.value} "
            f"across {len(target_regions)} regions with {self.max_workers} workers"
        )

        # Scan regions in parallel using ThreadPoolExecutor
        region_results = []

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all scan tasks
            futures = {
                executor.submit(self._scan_region, region, resource_type): region
                for region in target_regions
            }

            # Collect results as they complete
            for future in futures:
                try:
                    result = future.result()
                    region_results.append(result)
                except Exception as e:
                    region = futures[future]
                    logger.error(f"Exception scanning {region}: {e}")
                    region_results.append(
                        RegionScanResult(
                            region=region,
                            resource_type=resource_type,
                            scan_result=None,
                            success=False,
                            error=str(e)
                        )
                    )

        # Aggregate results
        total_resources = 0
        total_untagged = 0
        total_tagged = 0
        successful_scans = 0
        failed_scans = 0

        for result in region_results:
            if result.success and result.scan_result:
                successful_scans += 1
                total_resources += result.scan_result.total_resources
                total_untagged += len(result.scan_result.untagged_resources)
                total_tagged += len(result.scan_result.tagged_resources)
            else:
                failed_scans += 1

        # Calculate overall compliance rate
        compliance_rate = (
            (total_tagged / total_resources * 100)
            if total_resources > 0
            else 0.0
        )

        total_duration = time.time() - start_time

        multi_region_result = MultiRegionScanResult(
            resource_type=resource_type,
            regions_scanned=target_regions,
            successful_scans=successful_scans,
            failed_scans=failed_scans,
            total_resources=total_resources,
            total_untagged=total_untagged,
            total_tagged=total_tagged,
            tagging_compliance_rate=compliance_rate,
            region_results=region_results,
            total_scan_duration_seconds=total_duration
        )

        logger.info(
            f"Multi-region scan complete: {successful_scans}/{len(target_regions)} regions successful, "
            f"{total_resources} total resources, {total_untagged} untagged "
            f"({compliance_rate:.1f}% compliant) in {total_duration:.2f}s"
        )

        return multi_region_result

    async def scan_all_regions_async(
        self,
        resource_type: ResourceType,
        regions: Optional[List[str]] = None,
        use_all_regions: bool = False
    ) -> MultiRegionScanResult:
        """Async version of scan_all_regions using asyncio.

        Args:
            resource_type: Type of resource to scan
            regions: List of regions to scan
            use_all_regions: If True, scan ALL AWS regions

        Returns:
            MultiRegionScanResult
        """
        start_time = time.time()

        # Determine regions
        if use_all_regions:
            target_regions = self.ALL_REGIONS
        elif regions:
            target_regions = regions
        else:
            target_regions = self.COMMON_REGIONS

        logger.info(
            f"Starting async multi-region scan for {resource_type.value} "
            f"across {len(target_regions)} regions"
        )

        # Create async tasks for each region
        loop = asyncio.get_event_loop()
        tasks = []

        for region in target_regions:
            # Run sync scanner in executor
            task = loop.run_in_executor(
                None,
                self._scan_region,
                region,
                resource_type
            )
            tasks.append(task)

        # Wait for all tasks to complete
        region_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle any exceptions
        processed_results = []
        for i, result in enumerate(region_results):
            if isinstance(result, Exception):
                region = target_regions[i]
                logger.error(f"Async scan failed for {region}: {result}")
                processed_results.append(
                    RegionScanResult(
                        region=region,
                        resource_type=resource_type,
                        scan_result=None,
                        success=False,
                        error=str(result)
                    )
                )
            else:
                processed_results.append(result)

        # Aggregate results
        total_resources = 0
        total_untagged = 0
        total_tagged = 0
        successful_scans = 0
        failed_scans = 0

        for result in processed_results:
            if result.success and result.scan_result:
                successful_scans += 1
                total_resources += result.scan_result.total_resources
                total_untagged += len(result.scan_result.untagged_resources)
                total_tagged += len(result.scan_result.tagged_resources)
            else:
                failed_scans += 1

        compliance_rate = (
            (total_tagged / total_resources * 100)
            if total_resources > 0
            else 0.0
        )

        total_duration = time.time() - start_time

        return MultiRegionScanResult(
            resource_type=resource_type,
            regions_scanned=target_regions,
            successful_scans=successful_scans,
            failed_scans=failed_scans,
            total_resources=total_resources,
            total_untagged=total_untagged,
            total_tagged=total_tagged,
            tagging_compliance_rate=compliance_rate,
            region_results=processed_results,
            total_scan_duration_seconds=total_duration
        )

    def generate_multi_region_report(
        self,
        scan_result: MultiRegionScanResult
    ) -> Dict[str, Any]:
        """Generate comprehensive report from multi-region scan.

        Args:
            scan_result: Multi-region scan result

        Returns:
            Dictionary with detailed report
        """
        region_summary = scan_result.get_region_summary()

        # Find top offending regions
        regions_by_untagged = sorted(
            [
                (region, data)
                for region, data in region_summary.items()
                if 'error' not in data
            ],
            key=lambda x: x[1].get('untagged', 0),
            reverse=True
        )

        top_offenders = regions_by_untagged[:5]

        return {
            'scan_summary': {
                'resource_type': scan_result.resource_type.value,
                'regions_scanned': len(scan_result.regions_scanned),
                'successful_scans': scan_result.successful_scans,
                'failed_scans': scan_result.failed_scans,
                'total_resources': scan_result.total_resources,
                'total_untagged': scan_result.total_untagged,
                'total_tagged': scan_result.total_tagged,
                'compliance_rate': f"{scan_result.tagging_compliance_rate:.1f}%",
                'scan_duration': f"{scan_result.total_scan_duration_seconds:.2f}s"
            },
            'region_breakdown': region_summary,
            'top_offending_regions': [
                {
                    'region': region,
                    'untagged_count': data['untagged'],
                    'total_count': data['total'],
                    'compliance_rate': f"{data['compliance_rate']:.1f}%"
                }
                for region, data in top_offenders
            ],
            'recommendations': self._generate_recommendations(scan_result)
        }

    def _generate_recommendations(
        self,
        scan_result: MultiRegionScanResult
    ) -> List[str]:
        """Generate recommendations based on scan results.

        Args:
            scan_result: Multi-region scan result

        Returns:
            List of recommendations
        """
        recommendations = []

        # High number of untagged resources
        if scan_result.total_untagged > 100:
            recommendations.append(
                f"CRITICAL: {scan_result.total_untagged} untagged {scan_result.resource_type.value} "
                f"resources detected. Implement tag policies immediately."
            )

        # Low compliance rate
        if scan_result.tagging_compliance_rate < 50:
            recommendations.append(
                f"HIGH: Tagging compliance is only {scan_result.tagging_compliance_rate:.1f}%. "
                "Consider implementing automated tagging via AWS Config or Service Catalog."
            )

        # Failed scans
        if scan_result.failed_scans > 0:
            recommendations.append(
                f"MEDIUM: {scan_result.failed_scans} regions failed to scan. "
                "Check AWS credentials and IAM permissions."
            )

        # Specific resource type recommendations
        if scan_result.resource_type == ResourceType.S3:
            recommendations.append(
                "S3 SECURITY: Review untagged buckets for public access and encryption status."
            )
        elif scan_result.resource_type == ResourceType.RDS:
            recommendations.append(
                "RDS COMPLIANCE: Ensure all databases have DataClassification and Environment tags for compliance."
            )
        elif scan_result.resource_type == ResourceType.EBS:
            recommendations.append(
                "EBS COST: Check for orphaned unattached volumes to reduce storage costs."
            )

        return recommendations
