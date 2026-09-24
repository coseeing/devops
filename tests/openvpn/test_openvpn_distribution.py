from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


class CfnLoader(yaml.SafeLoader):
    pass


def construct_tag(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


CfnLoader.add_multi_constructor("!", construct_tag)


def load_template():
    path = ROOT / "cloudformation/openvpn-distribution-template.yml"
    return yaml.load(path.read_text(), Loader=CfnLoader)


def test_profile_bucket_is_private_encrypted_unversioned_and_short_lived() -> None:
    template = load_template()
    bucket = template["Resources"]["ProfileBucket"]["Properties"]
    assert bucket["PublicAccessBlockConfiguration"] == {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }
    assert bucket["OwnershipControls"]["Rules"] == [
        {"ObjectOwnership": "BucketOwnerEnforced"}
    ]
    assert bucket["BucketEncryption"]["ServerSideEncryptionConfiguration"][0][
        "ServerSideEncryptionByDefault"
    ]["SSEAlgorithm"] == "AES256"
    assert "VersioningConfiguration" not in bucket
    assert bucket["LifecycleConfiguration"]["Rules"][0]["ExpirationInDays"] == 1


def test_bucket_policy_enforces_tls_and_ten_minute_signature_age() -> None:
    statements = load_template()["Resources"]["ProfileBucketPolicy"]["Properties"][
        "PolicyDocument"
    ]["Statement"]
    by_sid = {statement["Sid"]: statement for statement in statements}
    assert by_sid["DenyInsecureTransport"]["Condition"] == {
        "Bool": {"aws:SecureTransport": "false"}
    }
    age = by_sid["DenyOldPresignedProfileDownloads"]
    assert age["Action"] == "s3:GetObject"
    assert age["Condition"]["NumericGreaterThan"]["s3:signatureAge"] == 600000


def test_linux_role_policy_has_only_required_profile_object_actions() -> None:
    policy = load_template()["Resources"]["ProfileDistributionPolicy"]["Properties"]
    assert policy["Roles"] == ["ExistingRoleName"]
    statement = policy["PolicyDocument"]["Statement"][0]
    assert set(statement["Action"]) == {
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
    }
    assert statement["Resource"] == "${ProfileBucket.Arn}/profiles/*"
