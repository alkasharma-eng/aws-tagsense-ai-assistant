"""
AWS Lambda Function Scanner for AWS TagSense.

This module provides Lambda function scanning with caching,
error handling, and comprehensive tag management.
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
from botocore.exceptions import ClientError

from tagger_core.resource_scanner import (
    BaseResourceScanner,
    AWSResource,
    ScanResult,
    ResourceType
)


logger = logging.getLogger(__name__)


class LambdaScanner(BaseResourceScanner):
    """Scanner for AWS Lambda functions.

    Provides enhanced functionality for scanning Lambda functions including:
    - Automatic retries with exponential backoff
    - Response caching
    - Detailed error handling
    - Runtime and version filtering

    Example:
        ```python
        scanner = LambdaScanner(region="us-east-1", profile="production")
        result = scanner.scan()

        print(f"Found {result.total_resources} Lambda functions")
        print(f"Untagged: {len(result.untagged_resources)}")

        # Tag untagged functions
        for function in result.untagged_resources:
            scanner.apply_tags(function.metadata['arn'], {
                "Environment": "production",
                "ManagedBy": "aws-tagsense",
                "CostCenter": "engineering"
            })
        ```
    """

    @property
    def client(self):
        """Get or create Lambda client."""
        if self._client is None:
            self._client = self.session.client('lambda')
        return self._client

    def get_resource_type(self) -> ResourceType:
        """Get the resource type.

        Returns:
            ResourceType.LAMBDA
        """
        return ResourceType.LAMBDA

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(ClientError),
        reraise=True
    )
    def scan(self, runtime_filter: Optional[List[str]] = None) -> ScanResult:
        """Scan for Lambda functions in the region.

        Args:
            runtime_filter: Optional list of runtimes to filter by
                           (e.g., ['python3.11', 'nodejs18.x']). If None, scans all runtimes.

        Returns:
            ScanResult containing all found Lambda functions

        Raises:
            ClientError: If the AWS API call fails after retries
        """
        try:
            logger.info(f"Scanning Lambda functions in {self.region}")

            functions = []
            marker = None

            # Lambda API is paginated
            while True:
                if marker:
                    response = self.client.list_functions(Marker=marker)
                else:
                    response = self.client.list_functions()

                # Parse functions
                for function_data in response.get('Functions', []):
                    # Apply runtime filter if specified
                    if runtime_filter and function_data.get('Runtime') not in runtime_filter:
                        continue

                    function = self._parse_function(function_data)
                    functions.append(function)

                # Check for more pages
                marker = response.get('NextMarker')
                if not marker:
                    break

            # Separate tagged and untagged
            tagged = [f for f in functions if f.is_tagged]
            untagged = [f for f in functions if not f.is_tagged]

            logger.info(
                f"Lambda scan complete: {len(functions)} total, "
                f"{len(untagged)} untagged"
            )

            return ScanResult(
                resource_type=ResourceType.LAMBDA,
                region=self.region,
                total_resources=len(functions),
                tagged_resources=tagged,
                untagged_resources=untagged,
                resources=functions
            )

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(f"Lambda scan failed: {error_code} - {str(e)}")
            raise

        except Exception as e:
            logger.error(f"Unexpected error during Lambda scan: {str(e)}")
            raise

    def _parse_function(self, function_data: Dict) -> AWSResource:
        """Parse Lambda function data into AWSResource object.

        Args:
            function_data: Raw function data from AWS API

        Returns:
            AWSResource object
        """
        function_name = function_data['FunctionName']
        function_arn = function_data['FunctionArn']

        # Parse tags (Lambda stores tags separately from function config)
        tags = {}
        try:
            tags_response = self.client.list_tags(Resource=function_arn)
            tags = tags_response.get('Tags', {})
        except ClientError as e:
            logger.warning(f"Could not fetch tags for {function_name}: {str(e)}")

        # Collect metadata
        metadata = {
            'arn': function_arn,
            'runtime': function_data.get('Runtime'),
            'handler': function_data.get('Handler'),
            'code_size': function_data.get('CodeSize'),
            'memory_size': function_data.get('MemorySize'),
            'timeout': function_data.get('Timeout'),
            'last_modified': function_data.get('LastModified'),
            'role': function_data.get('Role'),
            'description': function_data.get('Description', ''),
            'architectures': function_data.get('Architectures', []),
            'package_type': function_data.get('PackageType', 'Zip'),
        }

        # Lambda functions don't have a traditional "state" like EC2
        # We use version/last_modified to indicate "active"
        state = "active"

        return AWSResource(
            resource_id=function_name,
            resource_type=ResourceType.LAMBDA,
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
        resource_arn: str,  # Lambda uses ARN, not resource_id
        tags: Dict[str, str],
        dry_run: bool = False
    ) -> bool:
        """Apply tags to a Lambda function.

        Args:
            resource_arn: Lambda function ARN (not function name)
            tags: Dictionary of tags to apply
            dry_run: If True, validate without actually applying tags

        Returns:
            True if successful

        Raises:
            ClientError: If tagging fails
        """
        if dry_run:
            logger.info(f"Dry run: would tag Lambda function {resource_arn} with {len(tags)} tags")
            return True

        try:
            # Lambda tag_resource expects ARN and tag dict directly
            self.client.tag_resource(
                Resource=resource_arn,
                Tags=tags
            )

            logger.info(f"Successfully tagged Lambda function {resource_arn} with {len(tags)} tags")
            return True

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(f"Failed to tag Lambda function {resource_arn}: {error_code} - {str(e)}")
            raise

    def remove_tags(self, resource_arn: str, tag_keys: List[str]) -> bool:
        """Remove specific tags from a Lambda function.

        Args:
            resource_arn: Lambda function ARN
            tag_keys: List of tag keys to remove

        Returns:
            True if successful

        Raises:
            ClientError: If untagging fails
        """
        try:
            self.client.untag_resource(
                Resource=resource_arn,
                TagKeys=tag_keys
            )

            logger.info(f"Successfully removed {len(tag_keys)} tags from {resource_arn}")
            return True

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            logger.error(f"Failed to remove tags from {resource_arn}: {error_code} - {str(e)}")
            raise

    def scan_python_only(self) -> ScanResult:
        """Convenience method to scan only Python Lambda functions.

        Returns:
            ScanResult for Python functions
        """
        python_runtimes = ['python3.9', 'python3.10', 'python3.11', 'python3.12']
        return self.scan(runtime_filter=python_runtimes)

    def scan_nodejs_only(self) -> ScanResult:
        """Convenience method to scan only Node.js Lambda functions.

        Returns:
            ScanResult for Node.js functions
        """
        nodejs_runtimes = ['nodejs18.x', 'nodejs20.x']
        return self.scan(runtime_filter=nodejs_runtimes)

    def get_functions_by_tag(self, tag_key: str, tag_value: str) -> List[AWSResource]:
        """Get Lambda functions with a specific tag.

        Args:
            tag_key: Tag key to search for
            tag_value: Tag value to match

        Returns:
            List of functions with the specified tag
        """
        result = self.scan()
        return [
            func for func in result.resources
            if func.tags.get(tag_key) == tag_value
        ]
