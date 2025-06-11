import json
import os
import re
import shutil
from collections import OrderedDict
from enum import Enum
from logging import DEBUG

import pytest
import yaml
from cloudimg.aws import AWSBootMode
from mock import patch, MagicMock
from pushsource import AmiPushItem
from requests import HTTPError

from pubtools._adc.tasks.push import ADCPush, entry_point, LOG


AMI_STAGE_ROOT = "/tmp/aws_staged"  # nosec B108
AMI_SOURCE = "staged:%s" % AMI_STAGE_ROOT


def compare_metadata(metadata, exp_metadata):
    """
    Helper fction to compare metadata object with a dictionary of expected metadata
    """
    for key, value in exp_metadata.items():
        if getattr(metadata, key) != value:
            return False
    return True


@pytest.fixture(scope="session", autouse=True)
def stage_ami():
    if os.path.exists(AMI_STAGE_ROOT):
        shutil.rmtree(AMI_STAGE_ROOT)
    ami_dest = os.path.join(AMI_STAGE_ROOT, "region-1-hourly/AWS_IMAGES")
    os.makedirs(ami_dest, mode=0o777)
    open(os.path.join(ami_dest, "ami-1.raw"), "a").close()

    j_file = os.path.join(os.path.dirname(__file__), "data/aws_staged/pub-mapfile.json")
    with open(j_file, "r") as in_file:
        with open(os.path.join(AMI_STAGE_ROOT, "pub-mapfile.json"), "w") as out_file:
            data = json.load(in_file)
            json.dump(data, out_file)
    yield

    if os.path.exists(AMI_STAGE_ROOT):
        shutil.rmtree(AMI_STAGE_ROOT)


accounts = json.dumps({"default": {"access-1": "secret-1"}})
region_acc = json.dumps(
    {"region-1": {"access-r": "secret-r"}, "default": {"access-1": "secret-1"}}
)
snapshot_acc = json.dumps(
    {
        "region-1": ["0987654321", "1234567890", "684062674729"],
        "default": ["1300655506"],
    }
)


@pytest.fixture
def staged_file():
    staged_yaml = {
        "header": {"version": "0.2"},
        "payload": {
            "files": [
                {"filename": "test.txt", "relative_path": "test_x86_64/FILES/test.txt"}
            ]
        },
    }
    temp_stage = "/tmp/test_staged"  # nosec B108
    if os.path.exists(temp_stage):
        shutil.rmtree(temp_stage)
    os.makedirs(os.path.join(temp_stage, "test_x86_64/FILES"), mode=0o777)
    open(os.path.join(temp_stage, "test_x86_64/FILES/test.txt"), "a").close()
    with open(os.path.join(temp_stage, "staged.yml"), "w") as out_file:
        yaml.dump(staged_yaml, out_file)
    yield temp_stage
    if os.path.exists(temp_stage):
        shutil.rmtree(temp_stage)


@pytest.fixture(autouse=True)
def mock_aws_publish():
    with patch("pubtools._adc.services.aws.AWSService.publish") as m:
        publish_rv = MagicMock(id="ami-1234567")
        publish_rv.name = "ami-rhel"
        m.return_value = publish_rv
        yield m


@pytest.fixture()
def mock_ami_upload():
    with patch("pubtools._adc.tasks.push.ADCPush.upload") as m:
        yield m


@pytest.fixture()
def mock_region_data():
    with patch("pubtools._adc.tasks.base.AmiBase.region_data") as m:
        first_ami_data = {
            "name": "ami-01",
            "billing_codes": {"codes": ["code-0001"], "name": "Hourly2"},
            "boot_mode": "hybrid",
            "description": "Provided by Red Hat, Inc.",
            "ena_support": True,
            "region": "region-1",
            "release": {
                "arch": "x86_64",
                "base_product": "RHEL",
                "base_version": "8.5",
                "date": "20210902",
                "product": "RHEL",
                "respin": 5,
                "type": "beta",
                "variant": "BaseOS",
                "version": "8.5.0",
            },
            "root_device": "/dev/sda1",
            "sriov_net_support": "simple",
            "type": "hourly",
            "uefi_support": False,
            "virtualization": "hvm",
            "volume": "gp2",
        }

        second_ami_data = first_ami_data.copy()
        second_ami_data["name"] = "ami-02"

        first_ami = AmiPushItem._from_data(first_ami_data)
        second_ami = AmiPushItem._from_data(second_ami_data)

        region_data_rv = [[{"push_item": first_ami}, {"push_item": second_ami}]]
        m.return_value = region_data_rv
        yield m


@pytest.fixture(autouse=True)
def mock_debug_logger():
    # dicts are unordered in < py36. Hence, logging them may generate
    # a different order every time. The dicts are logged via debug
    # method in the code. LOG.debug is overridden to sort the dicts
    # before logging for the tests, generating a consistent sequence
    # of items everytime to match with the test_log data.
    def _log_debug(*args):
        debug_args = []
        for arg in args:
            if isinstance(arg, dict):
                od = OrderedDict(sorted(arg.items()))
                # json dumps can't handle Enums by default.
                # For testing it's fine to treat it as a string
                for key, val in od.items():
                    if isinstance(val, Enum):
                        od[key] = val.value
                debug_args.append(json.dumps(od))
            else:
                debug_args.append(arg)
        LOG._log(DEBUG, debug_args[0], tuple(debug_args[1:]))

    with patch("pubtools._adc.tasks.push.LOG.debug") as log_debug:
        log_debug.side_effect = _log_debug
        yield log_debug


def test_do_push(command_tester, requests_mocker, mock_aws_publish, fake_collector):
    """Successful push and ship of an AWS image."""
    requests_mocker.register_uri("PUT", re.compile("amazon/amis"), status_code=400)
    command_tester.test(
        lambda: entry_point(ADCPush),
        [
            "test-push",
            "--retry-wait",
            "1",
            "--accounts",
            region_acc,
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--snapshot-account-ids",
            snapshot_acc,
            "--ship",
            "--debug",
            AMI_SOURCE,
        ],
    )

    # Check that aws publish has been called once
    mock_aws_publish.assert_called_once()

    # Assert that correct metadata was used
    expected_metadata = {
        "ena_support": True,
        "sriov_net_support": "simple",
        "billing_products": ["code-0001"],
        "image_path": "/tmp/aws_staged/region-1-hourly/AWS_IMAGES/ami-1.raw",  # nosec B108
        "image_name": "RHEL-8.5-RHEL-8.5.0_HVM_BETA-20210902-x86_64-5-Hourly2-GP2",
        "snapshot_name": "RHEL-8.5-RHEL-8.5.0_HVM_BETA-20210902-x86_64-5-Hourly2-GP2",
        "snapshot_account_ids": ["0987654321", "1234567890", "684062674729"],
        "description": "Provided by Red Hat, Inc.",
        "container": "redhat-cloudimg-region-1",
        "arch": "x86_64",
        "virt_type": "hvm",
        "root_device_name": "/dev/sda1",
        "volume_type": "gp2",
        "accounts": ["secret-r"],
        "groups": [],
        "tags": None,
        "boot_mode": AWSBootMode.hybrid,
    }
    aws_publish_args, _ = mock_aws_publish.call_args_list[0]
    aws_metadata = aws_publish_args[0]
    assert compare_metadata(aws_metadata, expected_metadata)

    # Check state of items pushed
    stored_items = fake_collector.items
    assert len(stored_items) == 1
    assert "PUSHED" == stored_items[0]["state"]

    # Check contents of files pushed
    images_json = json.loads(fake_collector.file_content["images.json"])
    assert len(images_json) == 1
    assert "ami-1234567" == images_json[0]["ami"]
    assert "hybrid" == images_json[0]["boot_mode"]


def test_do_push_defaults(
    command_tester, requests_mocker, mock_aws_publish, fake_collector
):
    """Successful push with default ``account`` and ``snapshot_account``."""
    requests_mocker.register_uri("PUT", re.compile("amazon/amis"), status_code=400)
    command_tester.test(
        lambda: entry_point(ADCPush),
        [
            "test-push",
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--ship",
            "--debug",
            AMI_SOURCE,
        ],
    )

    # Check that aws publish has been called once
    mock_aws_publish.assert_called_once()

    # Assert that correct metadata was used
    expected_metadata = {
        "ena_support": True,
        "sriov_net_support": "simple",
        "billing_products": ["code-0001"],
        "image_path": "/tmp/aws_staged/region-1-hourly/AWS_IMAGES/ami-1.raw",  # nosec B108
        "image_name": "RHEL-8.5-RHEL-8.5.0_HVM_BETA-20210902-x86_64-5-Hourly2-GP2",
        "snapshot_name": "RHEL-8.5-RHEL-8.5.0_HVM_BETA-20210902-x86_64-5-Hourly2-GP2",
        "snapshot_account_ids": [],
        "description": "Provided by Red Hat, Inc.",
        "container": "redhat-cloudimg-region-1",
        "arch": "x86_64",
        "virt_type": "hvm",
        "root_device_name": "/dev/sda1",
        "volume_type": "gp2",
        "accounts": [],
        "groups": [],
        "tags": None,
        "boot_mode": AWSBootMode.hybrid,
    }
    aws_publish_args, _ = mock_aws_publish.call_args_list[0]
    aws_metadata = aws_publish_args[0]
    assert compare_metadata(aws_metadata, expected_metadata)

    # Check state of items pushed
    stored_items = fake_collector.items
    assert len(stored_items) == 1
    assert "PUSHED" == stored_items[0]["state"]

    # Check contents of files pushed
    images_json = json.loads(fake_collector.file_content["images.json"])
    assert len(images_json) == 1
    assert "ami-1234567" == images_json[0]["ami"]
    assert "hybrid" == images_json[0]["boot_mode"]


def test_no_source(command_tester, capsys):
    """Checks that exception is raised when the source is missing"""
    command_tester.test(
        lambda: entry_point(ADCPush),
        ["test-push", "--debug", "https://example.com"],
    )
    _, err = capsys.readouterr()
    assert (
        "error: too few arguments"
        or "error: the following arguments are required" in err
    )


def test_no_aws_credentials(command_tester):
    """Raises an error that AWS credentials were not provided to upload an image"""
    command_tester.test(
        lambda: entry_point(ADCPush),
        [
            "test-push",
            "--debug",
            "--accounts",
            accounts,
            "--snapshot-account-ids",
            snapshot_acc,
            "--retry-wait",
            "1",
            AMI_SOURCE,
        ],
    )


def test_push_public_image(
    command_tester, requests_mocker, mock_aws_publish, fake_collector
):
    """Successfully pushed images to all the accounts so it's available for general public"""
    command_tester.test(
        lambda: entry_point(ADCPush),
        [
            "test-push",
            "--retry-wait",
            "1",
            "--accounts",
            accounts,
            "--snapshot-account-ids",
            snapshot_acc,
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--ship",
            "--allow-public-image",
            "--debug",
            AMI_SOURCE,
        ],
    )

    # Check that aws publish has been called twice - once for normal publish
    # with restricted groups and once to publish public image
    assert len(mock_aws_publish.call_args_list) == 2

    # Assert that correct metadata was used
    expected_metadata = {
        "ena_support": True,
        "sriov_net_support": "simple",
        "billing_products": ["code-0001"],
        "image_path": "/tmp/aws_staged/region-1-hourly/AWS_IMAGES/ami-1.raw",  # nosec B108
        "image_name": "RHEL-8.5-RHEL-8.5.0_HVM_BETA-20210902-x86_64-5-Hourly2-GP2",
        "snapshot_name": "RHEL-8.5-RHEL-8.5.0_HVM_BETA-20210902-x86_64-5-Hourly2-GP2",
        "snapshot_account_ids": ["0987654321", "1234567890", "684062674729"],
        "description": "Provided by Red Hat, Inc.",
        "container": "redhat-cloudimg-region-1",
        "arch": "x86_64",
        "virt_type": "hvm",
        "root_device_name": "/dev/sda1",
        "volume_type": "gp2",
        "accounts": ["secret-1"],
        "groups": ["all"],
        "tags": None,
        "boot_mode": AWSBootMode.hybrid,
    }
    aws_publish_args, _ = mock_aws_publish.call_args_list[0]
    aws_metadata = aws_publish_args[0]
    assert compare_metadata(aws_metadata, expected_metadata)
    # Since the published image has groups set to 'all' initially, we get two
    # identical calls - one for public image and one with groups restricted to `all`
    assert len(set(aws_publish_args)) == 1

    # Check state of items pushed
    stored_items = fake_collector.items
    assert len(stored_items) == 1
    assert "PUSHED" == stored_items[0]["state"]

    # Check contents of files pushed
    images_json = json.loads(fake_collector.file_content["images.json"])
    assert len(images_json) == 1
    assert "ami-1234567" == images_json[0]["ami"]


def test_not_ami_push_item(command_tester, staged_file):
    """Non AMI pushitem is skipped from inclusion in push list"""
    temp_stage = "staged:%s" % staged_file

    command_tester.test(
        lambda: entry_point(ADCPush),
        [
            "test-push",
            "--retry-wait",
            "1",
            "--max-retries",
            "2",
            "--accounts",
            accounts,
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--debug",
            temp_stage,
        ],
    )


def test_aws_publish_failure_retry(
    command_tester, requests_mocker, mock_aws_publish, fake_collector
):
    """Image upload to AWS is retried on upload failure till it's pushed successfully
    or reached max retry count"""
    response = mock_aws_publish.return_value
    mock_aws_publish.side_effect = [
        Exception("Unable to publish"),
        response,
        Exception("Unable to publish"),
        response,
        response,
    ]
    command_tester.test(
        lambda: entry_point(ADCPush),
        [
            "test-push",
            "--retry-wait",
            "1",
            "--accounts",
            accounts,
            "--snapshot-account-ids",
            snapshot_acc,
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--ship",
            "--allow-public-image",
            "--debug",
            AMI_SOURCE,
        ],
    )

    # Check that aws publish has been called 5x
    assert len(mock_aws_publish.call_args_list) == 5

    # Assert that correct metadata was used
    expected_metadata = {
        "ena_support": True,
        "sriov_net_support": "simple",
        "billing_products": ["code-0001"],
        "image_path": "/tmp/aws_staged/region-1-hourly/AWS_IMAGES/ami-1.raw",  # nosec B108
        "image_name": "RHEL-8.5-RHEL-8.5.0_HVM_BETA-20210902-x86_64-5-Hourly2-GP2",
        "snapshot_name": "RHEL-8.5-RHEL-8.5.0_HVM_BETA-20210902-x86_64-5-Hourly2-GP2",
        "snapshot_account_ids": ["0987654321", "1234567890", "684062674729"],
        "description": "Provided by Red Hat, Inc.",
        "container": "redhat-cloudimg-region-1",
        "arch": "x86_64",
        "virt_type": "hvm",
        "root_device_name": "/dev/sda1",
        "volume_type": "gp2",
        "accounts": ["secret-1"],
        "groups": [],
        "tags": None,
        "boot_mode": AWSBootMode.hybrid,
    }
    aws_publish_args, _ = mock_aws_publish.call_args_list[0]
    aws_metadata = aws_publish_args[0]
    assert compare_metadata(aws_metadata, expected_metadata)
    # Assert all the call arguments are the same
    assert len(set(aws_publish_args)) == 1

    # Check state of items pushed
    stored_items = fake_collector.items
    assert len(stored_items) == 1
    assert "PUSHED" == stored_items[0]["state"]

    # Check contents of files pushed
    images_json = json.loads(fake_collector.file_content["images.json"])
    assert len(images_json) == 1
    assert "ami-1234567" == images_json[0]["ami"]


def test_publish_retry_multiple(command_tester, mock_region_data, mock_ami_upload):
    """
    Assert that when multiple images are pushed to the same region and one
    of them fails only the failed one is repushed.
    """
    # let the first image push be successful and fail once for the second image
    mock_ami_upload.side_effect = [
        "some_test_id",
        HTTPError("some_url", "some_code", "some_msg", "some_hdrs", "some_fp"),
        "another_test_id",
    ]

    command_tester.test(
        lambda: entry_point(ADCPush),
        [
            "test-push",
            "--retry-wait",
            "1",
            "--accounts",
            region_acc,
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--snapshot-account-ids",
            snapshot_acc,
            "--ship",
            "--debug",
            AMI_SOURCE,
        ],
    )

    # check that there are only three pushes - one successful for the first
    # image and one unsuccessful and one retry for the second one
    assert mock_ami_upload.call_count == 3
    # check that the first image has been pushed only once
    pushed_image_names = [x[0][0].name for x in mock_ami_upload.call_args_list]
    assert pushed_image_names == ["ami-01", "ami-02", "ami-02"]
