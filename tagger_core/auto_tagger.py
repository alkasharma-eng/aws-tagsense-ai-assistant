import boto3
import argparse
import json
from datetime import datetime

def get_ec2_client(region, profile_name):
    session = boto3.Session(profile_name=profile_name, region_name=region)
    return session.client("ec2")

def find_untagged_instances(ec2_client):
    response = ec2_client.describe_instances()
    untagged = []

    for reservation in response.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            instance_id = instance["InstanceId"]
            tags = instance.get("Tags", [])
            if not tags:  # None or []
                untagged.append({
                    "InstanceId": instance_id,
                    "State": instance["State"]["Name"],
                })

    return untagged

def apply_tag(ec2_client, instance_id, key, value):
    ec2_client.create_tags(
        Resources=[instance_id],
        Tags=[{"Key": key, "Value": value}]
    )

def main():
    parser = argparse.ArgumentParser(
        description="Scan for untagged EC2 instances and (optionally) apply a default tag."
    )
    parser.add_argument("--region", required=True, help="AWS region (e.g. us-west-2)")
    parser.add_argument("--profile", default="auto-tagger-dev", help="AWS CLI profile name")
    parser.add_argument("--tag", help='Tag to apply in KEY=VALUE format, e.g. "Environment=Dev"')
    parser.add_argument("--apply-tag", action="store_true",
                        help="If provided, will attempt to tag untagged instances")
    parser.add_argument("--log-file", default="sample_output.json",
                        help="Where to write scan results as JSON")
    args = parser.parse_args()

    # Parse tag if provided
    tag_key = None
    tag_value = None
    if args.tag:
        if "=" not in args.tag:
            raise ValueError("Tag must be in KEY=VALUE format, e.g. Environment=Dev")
        tag_key, tag_value = args.tag.split("=", 1)

    ec2 = get_ec2_client(args.region, args.profile)

    print(f"\n[auto-tagger] Scanning region {args.region} using profile {args.profile} ...")
    untagged = find_untagged_instances(ec2)

    if not untagged:
        print("✅ No untagged instances found.")
    else:
        print(f"⚠ Found {len(untagged)} untagged instance(s):")
        for info in untagged:
            print(f"  - {info['InstanceId']} (state: {info['State']})")

        # Apply tags if requested
        if args.apply-tag:
            if not tag_key or not tag_value:
                raise ValueError("--apply-tag requires --tag KEY=VALUE")
            print("\n[auto-tagger] Applying tag to untagged instances...")
            for info in untagged:
                instance_id = info["InstanceId"]
                apply_tag(ec2, instance_id, tag_key, tag_value)
                print(f"   → Tagged {instance_id} with {tag_key}={tag_value}")

    # Write log file for audit / demo
    log_payload = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "region": args.region,
        "profile": args.profile,
        "untagged_instances": untagged,
    }

    with open(args.log_file, "w") as f:
        json.dump(log_payload, f, indent=2)

    print(f"\n[auto-tagger] Scan results written to {args.log_file}\n")

if __name__ == "__main__":
    main()
