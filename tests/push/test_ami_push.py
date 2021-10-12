import os
import re
import shutil
import pytest
import json
import yaml
from mock import patch, MagicMock
from pubtools._ami.tasks.push import AmiPush, entry_point

AMI_STAGE_ROOT = "/tmp/aws_staged"


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
    temp_stage = "/tmp/test_staged"
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
    with patch("pubtools._ami.services.aws.AWSService.publish") as m:
        publish_rv = MagicMock(id="ami-1234567")
        publish_rv.name = "ami-rhel"
        m.return_value = publish_rv
        yield m


@pytest.fixture(autouse=True)
def mock_rhsm_api(requests_mocker):
    requests_mocker.register_uri(
        "GET",
        re.compile("amazon/provider_image_groups"),
        json={"body": [{"name": "RHEL_HOURLY", "providerShortName": "awstest"}]},
    )
    requests_mocker.register_uri("POST", re.compile("amazon/region"))
    requests_mocker.register_uri("PUT", re.compile("amazon/amis"))
    requests_mocker.register_uri("POST", re.compile("amazon/amis"))


def test_do_push(command_tester, requests_mocker):
    requests_mocker.register_uri("PUT", re.compile("amazon/amis"), status_code=400)

    command_tester.test(
        lambda: entry_point(AmiPush),
        [
            "test-push",
            "--rhsm-url",
            "https://example.com",
            "--aws-provider-name",
            "awstest",
            "--retry-wait",
            "1",
            "--accounts",
            "123, 456",
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--ship",
            "--debug",
            AMI_STAGE_ROOT,
        ],
    )


def test_no_source(command_tester):

    command_tester.test(
        lambda: entry_point(AmiPush),
        ["test-push", "--debug", "--rhsm-url", "https://example.com"],
    )


def test_no_rhsm_url(command_tester):
    command_tester.test(
        lambda: entry_point(AmiPush),
        ["test-push", "--debug", AMI_STAGE_ROOT],
    )


def test_no_aws_credentials(command_tester):
    command_tester.test(
        lambda: entry_point(AmiPush),
        [
            "test-push",
            "--debug",
            "--rhsm-url",
            "https://example.com",
            "--aws-provider-name",
            "awstest",
            "--retry-wait",
            "1",
            AMI_STAGE_ROOT,
        ],
    )


def test_missing_product(command_tester):
    command_tester.test(
        lambda: entry_point(AmiPush),
        [
            "test-push",
            "--rhsm-url",
            "https://example.com",
            "--aws-provider-name",
            "AWS",
            "--retry-wait",
            "1",
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--debug",
            AMI_STAGE_ROOT,
        ],
    )


def test_push_public_image(command_tester):
    command_tester.test(
        lambda: entry_point(AmiPush),
        [
            "test-push",
            "--rhsm-url",
            "https://example.com",
            "--aws-provider-name",
            "awstest",
            "--retry-wait",
            "1",
            "--accounts",
            "123, 456",
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--ship",
            "--allow-public-image",
            "--debug",
            AMI_STAGE_ROOT,
        ],
    )


def test_create_region_failure(command_tester, requests_mocker):
    requests_mocker.register_uri("POST", re.compile("amazon/region"), status_code=500)
    command_tester.test(
        lambda: entry_point(AmiPush),
        [
            "test-push",
            "--rhsm-url",
            "https://example.com",
            "--aws-provider-name",
            "awstest",
            "--retry-wait",
            "1",
            "--accounts",
            "123, 456",
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--ship",
            "--debug",
            AMI_STAGE_ROOT,
        ],
    )


def test_create_image_failure(command_tester, requests_mocker):
    requests_mocker.register_uri("PUT", re.compile("amazon/amis"), status_code=400)
    requests_mocker.register_uri("POST", re.compile("amazon/amis"), status_code=500)
    command_tester.test(
        lambda: entry_point(AmiPush),
        [
            "test-push",
            "--rhsm-url",
            "https://example.com",
            "--aws-provider-name",
            "awstest",
            "--retry-wait",
            "1",
            "--max-retries",
            "2",
            "--accounts",
            "123, 456",
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--ship",
            "--debug",
            AMI_STAGE_ROOT,
        ],
    )


def test_not_ami_push_item(command_tester, staged_file):
    temp_stage = staged_file

    command_tester.test(
        lambda: entry_point(AmiPush),
        [
            "test-push",
            "--rhsm-url",
            "https://example.com",
            "--aws-provider-name",
            "awstest",
            "--retry-wait",
            "1",
            "--max-retries",
            "2",
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--debug",
            temp_stage,
        ],
    )


def test_aws_publish_failures(command_tester, mock_aws_publish):
    response = mock_aws_publish.return_value
    mock_aws_publish.side_effect = [
        Exception("Unable to publish"),
        response,
        Exception("Unable to publish"),
        response,
        response,
    ]
    command_tester.test(
        lambda: entry_point(AmiPush),
        [
            "test-push",
            "--rhsm-url",
            "https://example.com",
            "--aws-provider-name",
            "awstest",
            "--retry-wait",
            "1",
            "--accounts",
            "123, 456",
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--ship",
            "--allow-public-image",
            "--debug",
            AMI_STAGE_ROOT,
        ],
    )
