import json
import logging
import os
import attr
from cloudimg.aws import AWSPublishingMetadata
from more_executors import Executors, ExceptionRetryPolicy
from requests import HTTPError

from ..services import AWSPublishService, CollectorService
from ..task import AmiTask
from .base import AmiBase
from .exceptions import AWSPublishError


LOG = logging.getLogger("pubtools.adc")

step = AmiTask.step


class ADCPush(AmiBase, AWSPublishService, CollectorService):
    """Pushes one or more Amazon Machine Images to AWS from the specified sources.

    This command gets the AMIs from the provided sources and then uploads to AWS using the
    image metadata from the source. The image metadata is then updated to the metadata service
    post upload if the images were shipped to the users.
    """

    _REQUEST_THREADS = int(os.environ.get("AMI_PUSH_REQUEST_THREADS", "5"))

    def __init__(self, *args, **kwargs):
        self._ami_push_items = None
        super(ADCPush, self).__init__(*args, **kwargs)

    @step("Upload image to AWS")
    # pylint:disable=too-many-locals
    def upload(self, push_item):
        """
        Uploads and imports a disk image to AWS. If ship is not True, the image
        will only be available to internal accounts. Returns a tuple of the
        image id as provided by Amazon and its name.

        All the work is handled by the cloudimg library and it can take a
        considerable amount of time. The general workflow is as follows.

        1) Upload the bits via HTTP to AWS storage (S3)
        2) Import the uploaded file as an EC2 snapshot
        3) Register the snapshot as an AWS image (AMI)
        4) Modify permissions for the AMI

        Steps 1 and 2 will only be performed once per region per file.

        Different AMIs may be registered from the same snapshot during step 3.
        Each image type (hourly, access, etc) produces its own AMI.

        It is advantageous to not call this method in parallel for the same
        file because the cloudimg library is smart enough to skip lengthy steps
        such as the upload and snapshot registration.
        """
        region = push_item.region
        image_type = push_item.type
        file_path = push_item.src
        ship = self.args.ship
        LOG.info(
            "Uploading %s to region %s (type: %s, ship: %s)",
            file_path,
            region,
            image_type,
            ship,
        )

        name = self.name_from_metadata(push_item)
        LOG.info("Image name: %s", name)

        container = "%s-%s" % (self.args.container_prefix, region)
        _accounts = self.args.accounts
        if region in _accounts:
            accounts = list(_accounts[region].values())
        else:
            accounts = list(_accounts["default"].values())

        snapshot_account_ids = (
            self.args.snapshot_account_ids[region]
            if region in self.args.snapshot_account_ids
            else self.args.snapshot_account_ids["default"]
        )
        boot_mode = push_item.boot_mode.value if push_item.boot_mode else None
        publishing_meta_kwargs = {
            "image_path": file_path,
            "image_name": name,
            "snapshot_name": name,
            "container": container,
            "description": push_item.description,
            "arch": push_item.release.arch,
            "virt_type": push_item.virtualization,
            "root_device_name": push_item.root_device,
            "volume_type": push_item.volume,
            "boot_mode": boot_mode,
            "billing_products": push_item.billing_codes.codes,
            "accounts": accounts,
            "snapshot_account_ids": snapshot_account_ids,
            "sriov_net_support": push_item.sriov_net_support,
            "ena_support": push_item.ena_support or False,
        }
        LOG.debug("%s", publishing_meta_kwargs)
        publish_meta = AWSPublishingMetadata(**publishing_meta_kwargs)

        aws = self.aws_service(region)
        try:
            image = aws.publish(publish_meta)
        except Exception as exc:  # pylint:disable=broad-except
            raise AWSPublishError(exc) from exc

        if ship:
            public_image = push_item.public_image
            if public_image is None and image_type == "hourly":
                # Backwards compatibility for push items without public_image flag.
                # The "all" group grants access to the general public. This
                # should only be done for hourly images since they are the only
                # type to charge an additional Red Hat fee.
                # http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/sharingamis-intro.html
                public_image = True

            if public_image and self.args.allow_public_images:
                LOG.info("Releasing image %s publicly", image.id)
                publish_meta.groups = ["all"]
                # A repeat call to publish will only update the groups
                try:
                    aws.publish(publish_meta)
                except Exception as exc:  # pylint:disable=broad-except
                    raise AWSPublishError(exc) from exc

        LOG.info("Successfully uploaded %s [%s] [%s]", name, region, image.id)

        return image.id

    def _push_to_region(self, region_data):
        # sends the push_items for each region to be uploaded in
        # a single thread
        for dest_data in region_data:
            # Avoid repushing images successfully pushed when retrying
            if dest_data.get("state", "") == "PUSHED":
                continue
            push_item = dest_data["push_item"]
            image_id = None

            # Log exceptions from uploads
            try:
                image_id = self.upload(push_item)
            except Exception:
                LOG.warning("Upload failed", exc_info=True)
                raise
            state = "PUSHED"
            dest_data["push_item"] = attr.evolve(push_item, state=state)
            dest_data["state"] = state
            dest_data["image_id"] = image_id
            dest_data["image_name"] = self.name_from_metadata(push_item)
        return region_data

    def add_args(self):
        super(ADCPush, self).add_args()

        group = self.parser.add_argument_group("AMI Push options")

        group.add_argument(
            "--ship", help="publish the AMIs in public domain", action="store_true"
        )

        group.add_argument(
            "--container-prefix",
            help="prefix to storage container for upload",
            default="redhat-cloudimg",
        )

        group.add_argument(
            "--accounts",
            help="region to accounts mapping for the accounts which will have permission to use the image in a region. "
            "If the accounts are not specific to a region, map them to defaults "
            'e.g. \'{"region-1": {"user-1": "key-1"}}\' OR \'{"default": {"user-1": "key-1"}}\' '
            'OR \'{"region-1": {"user-1": "key-1"}, "default": {"user-1": "key-1"}}\'',
            type=json.loads,
            default={"default": {}},
        )

        group.add_argument(
            "--allow-public-images",
            help="images are released for general use",
            action="store_true",
        )

        group.add_argument(
            "--snapshot-account-ids",
            help="JSON string mapping region to a list of account ids to give "
            "snapshot creation permissions to if a new snapshot is created "
            "as part of the image push.",
            type=json.loads,
            default={"default": []},
        )

    def run(self):
        failed = False

        # split push_items into regions
        region_data = self.region_data()

        # upload
        with Executors.thread_pool(
            name="pubtools-adc-push",
            max_workers=min(len(region_data), self._REQUEST_THREADS),
        ).with_retry(
            logger=LOG,
            retry_policy=ExceptionRetryPolicy(
                sleep=self.args.retry_wait,
                max_attempts=self.args.max_retries,
                exception_base=(HTTPError, AWSPublishError),
            ),
        ) as executor:
            to_await = []
            result = []
            for data in region_data:
                to_await.append(executor.submit(self._push_to_region, data))

            # waiting for results
            for f_out in to_await:
                try:
                    result.extend(f_out.result())
                except Exception as exc:  # pylint:disable=broad-except
                    LOG.exception("AMI upload failed:", exc_info=exc)
                    failed = True

        # send to collector
        LOG.info("Collecting results")
        self.collect_push_result(result)

        if failed:
            self.fail("AMI upload failed")

        LOG.info("AMI upload completed")


def entry_point(cls=ADCPush):
    cls().main()


def doc_parser():
    return ADCPush().parser
