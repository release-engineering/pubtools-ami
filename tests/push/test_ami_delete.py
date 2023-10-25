import json
import re
import pytest

from mock import patch
from pushsource import AmiBillingCodes, AmiPushItem, AmiRelease, Source

from pubtools._ami.tasks.delete import AmiDelete, entry_point


@pytest.fixture()
def mock_aws_delete():
    with patch("pubtools._ami.services.aws.AWSService.delete") as m:
        yield m


def _setup_rhsm_api_mocks(
    requests_mocker,
    rhsm_ami_ids,
    dry_run=False,
):
    requests_mocker.register_uri(
        "GET",
        "/v1/internal/cloud_access_providers/amazon/amis",
        [
            {
                "json": {
                    "pagination": {"count": len(rhsm_ami_ids)},
                    "body": [{"amiID": ami_id} for ami_id in rhsm_ami_ids],
                },
                "status_code": 200,
            },
            {
                "json": {
                    "pagination": {"count": 0},
                    "body": [{}],
                },
                "status_code": 200,
            },
        ],
    )

    requests_mocker.register_uri(
        "GET",
        "/v1/internal/cloud_access_providers/amazon/provider_image_groups",
        status_code=200,
        json={"body": [{"name": "fake-product", "providerShortName": "awstest"}]},
    )

    if not dry_run:
        requests_mocker.register_uri("PUT", re.compile("amazon/amis"))


@pytest.mark.parametrize(
    "keep_snapshot", [False, True], ids=["keep_snapshot_false", "keep_snapshot_true"]
)
def test_ami_delete_typical(
    command_tester,
    requests_mocker,
    mock_aws_delete,
    keep_snapshot,
    fake_collector,
):
    """
    Tests basic AMI delete workflow. Image is updated in rhsm and image with snapshot
    are deleted from AWS. Also tests a variant with --keep-snapshot arg provided.
    """

    fake_items = [
        AmiPushItem(
            name="fake-name",
            image_id="ami-fake-id-01",
            description="fake-descr-01",
            type="fake-type",
            region="awstest",
            dest=["fake-dest"],
            virtualization="fake-virt",
            volume="fake-volume",
            billing_codes=AmiBillingCodes(name="fake-bc", codes=["0", "1"]),
            release=AmiRelease(
                product="fake-product", date="20230306", arch="fake-arch", respin=1
            ),
        ),
        AmiPushItem(
            name="fake-name",
            image_id="ami-fake-id-02",
            description="fake-descr-02",
            type="fake-type",
            region="awstest",
            dest=["fake-dest"],
            virtualization="fake-virt",
            volume="fake-volume",
            billing_codes=AmiBillingCodes(name="fake-bc", codes=["0", "1"]),
            release=AmiRelease(
                product="fake-product", date="20230306", arch="fake-arch", respin=1
            ),
        ),
    ]

    Source.register_backend("fake", lambda: fake_items)

    mock_aws_delete.return_value = (
        "ami-fake-id-01",
        None if keep_snapshot else "snap-fake-id-01",
    )
    _setup_rhsm_api_mocks(requests_mocker, ["ami-fake-id-01"])

    cmd_args = [
        "test-delete",
        "--rhsm-url",
        "https://rhsm.example.com",
        "fake:",
        "--aws-access-id",
        "access_id",
        "--aws-secret-key",
        "secret_key",
        "--debug",
        "--aws-provider-name",
        "awstest",
        "--limit",
        "ami-fake-id-01",
    ]

    if keep_snapshot:
        cmd_args.append("--keep-snapshot")
    command_tester.test(lambda: entry_point(AmiDelete), cmd_args)

    mock_aws_delete.assert_called_once()
    meta = mock_aws_delete.call_args[0][0]

    # check what metadata was passed to AWS API call
    assert meta.image_id == "ami-fake-id-01"
    assert meta.skip_snapshot is keep_snapshot

    # check stored push items and images.json file content
    stored_push_items = fake_collector.items

    assert len(stored_push_items) == 1
    pushitem = stored_push_items[0]
    assert pushitem["filename"] == "ami-fake-id-01"
    assert pushitem["state"] == "DELETED"

    images_json = json.loads(fake_collector.file_content["images.json"])
    assert images_json[0]["image_id"] == "ami-fake-id-01"
    assert images_json[0]["snapshot_id"] == None if keep_snapshot else "snap-fake-id-01"


def test_ami_delete_missing_image_in_aws(
    requests_mocker,
    command_tester,
    mock_aws_delete,
    fake_collector,
):
    """
    Tests basic AMI delete workflow when the image requseted for deletions is missing from AWS.
    Image is updated in rhsm and but skipped from deletion in AWS.
    """

    fake_items = [
        AmiPushItem(
            name="fake-name",
            image_id="ami-fake-id-01",
            description="fake-descr-01",
            type="fake-type",
            region="awstest",
            dest=["fake-dest"],
            virtualization="fake-virt",
            volume="fake-volume",
            billing_codes=AmiBillingCodes(name="fake-bc", codes=["0", "1"]),
            release=AmiRelease(
                product="fake-product", date="20230306", arch="fake-arch", respin=1
            ),
        ),
    ]

    Source.register_backend("fake", lambda: fake_items)

    mock_aws_delete.return_value = (
        None,
        None,
    )
    _setup_rhsm_api_mocks(requests_mocker, ["ami-fake-id-01"])

    cmd_args = [
        "test-delete",
        "--rhsm-url",
        "https://rhsm.example.com",
        "fake:",
        "--aws-access-id",
        "access_id",
        "--aws-secret-key",
        "secret_key",
        "--debug",
        "--aws-provider-name",
        "awstest",
        "--limit",
        "ami-fake-id-01",
    ]

    command_tester.test(lambda: entry_point(AmiDelete), cmd_args)

    mock_aws_delete.assert_called_once()
    meta = mock_aws_delete.call_args[0][0]

    # check what metadata was passed to AWS API call
    assert meta.image_id == "ami-fake-id-01"

    # check stored push items and images.json file content
    stored_push_items = fake_collector.items

    assert len(stored_push_items) == 1
    pushitem = stored_push_items[0]
    assert pushitem["filename"] == "ami-fake-id-01"
    assert pushitem["state"] == "MISSING"

    images_json = json.loads(fake_collector.file_content["images.json"])
    assert images_json[0]["image_id"] == "ami-fake-id-01"
    assert images_json[0]["snapshot_id"] is None


def test_ami_delete_dry_run(
    command_tester, requests_mocker, mock_aws_delete, fake_collector
):
    """
    Tests dry run for pubtools-ami-delete, no destructive action is done.
    """

    fake_items = [
        AmiPushItem(
            name="fake-name-01",
            image_id="ami-fake-id-01",
            description="fake-descr-01",
            type="fake-type",
            region="awstest",
            dest=["fake-dest"],
            virtualization="fake-virt",
            volume="fake-volume",
            billing_codes=AmiBillingCodes(name="fake-bc", codes=["0", "1"]),
            release=AmiRelease(
                product="fake-product", date="20230306", arch="fake-arch", respin=1
            ),
        ),
        AmiPushItem(
            name="fake-name-02",
            image_id="ami-fake-id-02",
            description="fake-descr-02",
            type="fake-type",
            region="awstest",
            dest=["fake-dest"],
            virtualization="fake-virt",
            volume="fake-volume",
            billing_codes=AmiBillingCodes(name="fake-bc", codes=["0", "1"]),
            release=AmiRelease(
                product="fake-product", date="20230306", arch="fake-arch", respin=1
            ),
        ),
    ]

    Source.register_backend("fake", lambda: fake_items)

    _setup_rhsm_api_mocks(
        requests_mocker,
        ["ami-fake-id-01"],
        dry_run=True,
    )

    command_tester.test(
        lambda: entry_point(AmiDelete),
        [
            "test-delete",
            "--dry-run",
            "--rhsm-url",
            "https://rhsm.example.com",
            "fake:",
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--debug",
            "--aws-provider-name",
            "awstest",
        ],
    )

    # delete in AWS shouldn't have been called at all
    mock_aws_delete.assert_not_called()

    # check stored push items and images.json file content
    stored_push_items = fake_collector.items

    assert len(stored_push_items) == 0
    assert fake_collector.file_content.get("images.json") is None


def test_ami_delete_missing_image_in_rhsm(
    command_tester, requests_mocker, mock_aws_delete, fake_collector
):
    """
    Tests a variant when image is not found is rhsm,
    in this case processing continues only with warning logged.
    """

    fake_items = [
        AmiPushItem(
            name="fake-name",
            image_id="ami-fake-id-01-not-in-rhsm",
            description="fake-descr-01",
            type="fake-type",
            region="awstest",
            dest=["fake-dest"],
            virtualization="fake-virt",
            volume="fake-volume",
            billing_codes=AmiBillingCodes(name="fake-bc", codes=["0", "1"]),
            release=AmiRelease(
                product="fake-product", date="20230306", arch="fake-arch", respin=1
            ),
        ),
    ]

    Source.register_backend("fake", lambda: fake_items)

    _setup_rhsm_api_mocks(
        requests_mocker,
        ["ami-fake-id-unknown"],
    )

    mock_aws_delete.return_value = (
        "ami-fake-id-01-not-in-rhsm",
        "snap-fake-id-01-not-in-rhsm",
    )
    command_tester.test(
        lambda: entry_point(AmiDelete),
        [
            "test-delete",
            "--rhsm-url",
            "https://example.com",
            "fake:",
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--debug",
            "--aws-provider-name",
            "awstest",
        ],
    )

    mock_aws_delete.assert_called_once()
    meta = mock_aws_delete.call_args[0][0]

    # check what metadata was passed to AWS API call
    assert meta.image_id == "ami-fake-id-01-not-in-rhsm"
    assert meta.skip_snapshot is False

    # check stored push items and images.json file content
    stored_push_items = fake_collector.items

    assert len(stored_push_items) == 1
    pushitem = stored_push_items[0]
    assert pushitem["filename"] == "ami-fake-id-01-not-in-rhsm"
    assert pushitem["state"] == "DELETED"

    images_json = json.loads(fake_collector.file_content["images.json"])
    assert images_json[0]["image_id"] == "ami-fake-id-01-not-in-rhsm"
    assert images_json[0]["snapshot_id"] == "snap-fake-id-01-not-in-rhsm"


def test_ami_delete_aws_failure(
    command_tester, requests_mocker, mock_aws_delete, fake_collector
):
    """
    Tests a variant when error is raised during deletion on AWS.
    """

    fake_items = [
        AmiPushItem(
            name="fake-name",
            image_id="ami-fake-id-01-aws-failure",
            description="fake-descr-01",
            type="fake-type",
            region="awstest",
            dest=["fake-dest"],
            virtualization="fake-virt",
            volume="fake-volume",
            billing_codes=AmiBillingCodes(name="fake-bc", codes=["0", "1"]),
            release=AmiRelease(
                product="fake-product", date="20230306", arch="fake-arch", respin=1
            ),
        ),
    ]

    Source.register_backend("fake", lambda: fake_items)

    _setup_rhsm_api_mocks(
        requests_mocker,
        ["ami-fake-id-01-aws-failure"],
    )

    mock_aws_delete.side_effect = Exception("AWS operation failed miserably!")

    cmd_args = [
        "test-delete",
        "--rhsm-url",
        "https://rhsm.example.com",
        "fake:",
        "--aws-access-id",
        "access_id",
        "--aws-secret-key",
        "secret_key",
        "--debug",
        "--aws-provider-name",
        "awstest",
        "--retry-wait",
        "1",
        "--max-retries",
        "2",
    ]

    command_tester.test(lambda: entry_point(AmiDelete), cmd_args)

    # mock was called twice due to retries
    assert len(mock_aws_delete.call_args_list) == 2

    for call_args in mock_aws_delete.call_args_list:
        meta = call_args[0][0]

        # check what metadata was passed to AWS API call
        assert meta.image_id == "ami-fake-id-01-aws-failure"
        assert meta.skip_snapshot is False

    # check stored push items and images.json file content
    stored_push_items = fake_collector.items

    assert len(stored_push_items) == 0
    assert len(json.loads(fake_collector.file_content.get("images.json"))) == 0


def test_ami_delete_rhsm_failure(
    command_tester, requests_mocker, mock_aws_delete, fake_collector
):
    """
    Tests a variant when error is raised during update on rhsm.
    """

    fake_items = [
        AmiPushItem(
            name="fake-name",
            image_id="ami-fake-id-01-rhsm-failure",
            description="fake-descr-01",
            type="fake-type",
            region="awstest",
            dest=["fake-dest"],
            virtualization="fake-virt",
            volume="fake-volume",
            billing_codes=AmiBillingCodes(name="fake-bc", codes=["0", "1"]),
            release=AmiRelease(
                product="fake-product", date="20230306", arch="fake-arch", respin=1
            ),
        ),
    ]
    Source.register_backend("fake", lambda: fake_items)

    _setup_rhsm_api_mocks(
        requests_mocker,
        ["ami-fake-id-01-rhsm-failure"],
    )

    # override mocker for rhsm for PUT req.
    requests_mocker.register_uri("PUT", re.compile("amazon/amis"), status_code=500)

    cmd_args = [
        "test-delete",
        "--rhsm-url",
        "https://rhsm.example.com",
        "fake:",
        "--aws-access-id",
        "access_id",
        "--aws-secret-key",
        "secret_key",
        "--debug",
        "--aws-provider-name",
        "awstest",
    ]

    command_tester.test(lambda: entry_point(AmiDelete), cmd_args)

    mock_aws_delete.assert_not_called()
    # check stored push items and images.json file content
    stored_push_items = fake_collector.items

    assert len(stored_push_items) == 0
    assert fake_collector.file_content.get("images.json") is None


def test_ami_delete_limit(
    command_tester, requests_mocker, mock_aws_delete, fake_collector
):
    """
    Tests a variant when no push item is left for processing
    when --limit args is used.
    """

    fake_items = [
        AmiPushItem(
            name="fake-name-01",
            image_id="ami-fake-id-01",
            description="fake-descr-01",
            type="fake-type",
            region="awstest",
            dest=["fake-dest"],
            virtualization="fake-virt",
            volume="fake-volume",
            billing_codes=AmiBillingCodes(name="fake-bc", codes=["0", "1"]),
            release=AmiRelease(
                product="fake-product", date="20230306", arch="fake-arch", respin=1
            ),
        ),
        AmiPushItem(
            name="fake-name-02",
            image_id="ami-fake-id-02",
            description="fake-descr-01",
            type="fake-type",
            region="awstest",
            dest=["fake-dest"],
            virtualization="fake-virt",
            volume="fake-volume",
            billing_codes=AmiBillingCodes(name="fake-bc", codes=["0", "1"]),
            release=AmiRelease(
                product="fake-product", date="20230306", arch="fake-arch", respin=1
            ),
        ),
    ]
    Source.register_backend("fake", lambda: fake_items)

    _setup_rhsm_api_mocks(
        requests_mocker,
        ["ami-fake-id-01", "ami-fake-id-02"],
    )

    command_tester.test(
        lambda: entry_point(AmiDelete),
        [
            "test-delete",
            "--rhsm-url",
            "https://example.com",
            "fake:",
            "--aws-access-id",
            "access_id",
            "--aws-secret-key",
            "secret_key",
            "--debug",
            "--aws-provider-name",
            "awstest",
            "--limit",
            "ami-id-wanted",
        ],
    )

    mock_aws_delete.assert_not_called()

    stored_push_items = fake_collector.items

    assert len(stored_push_items) == 0
    assert fake_collector.file_content.get("images.json") is None
