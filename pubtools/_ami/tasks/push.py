import logging
import sys
import os
import time
import datetime
import json
import attr

from requests import HTTPError
from six import raise_from
from more_executors import Executors
from cloudimg.aws import AWSPublishingMetadata
from pushsource import Source, AmiPushItem
from pubtools._ami.task import AmiTask
from pubtools._ami.arguments import SplitAndExtend
from ..services import RHSMClientService, AWSPublishService, CollectorService

LOG = logging.getLogger("pubtools.ami")


class MissingProductError(Exception):
    """Exception class for products missing in the metadata service"""


class AWSPublishError(Exception):
    """Exception class for AWS publish errors"""


class AmiPush(AmiTask, RHSMClientService, AWSPublishService, CollectorService):
    """Pushes one or more Amazon Machine Images to AWS from the specified sources.

    This command gets the AMIs from the provided sources, checks for the image product in
    the metadata service e.g. RHSM and then uploads to AWS using the image metadata from
    the source. The image metadata is then updated to the metadata service post upload if
    the images were shipped to the users.
    """

    _REQUEST_THREADS = int(os.environ.get("AMI_PUSH_REQUEST_THREADS", "5"))

    def __init__(self, *args, **kwargs):
        self._ami_push_items = None
        self._rhsm_products = None
        super(AmiPush, self).__init__(*args, **kwargs)

    @property
    def ami_push_items(self):
        if not self._ami_push_items:
            self._ami_push_items = self._get_push_items()
        return self._ami_push_items or None

    def fail(self, *args, **kwargs):
        LOG.error(*args, **kwargs)
        sys.exit(30)

    def _get_push_items(self):
        ami_push_items = []

        for source_loc in self.args.source:
            with Source.get(source_loc) as source:
                for push_item in source:
                    if not isinstance(push_item, AmiPushItem):
                        LOG.warning(
                            "Push Item %s at %s is not an AmiPushItem. "
                            "Dropping it from the queue",
                            push_item.name,
                            push_item.src,
                        )
                        continue
                    ami_push_items.append(push_item)
        return ami_push_items

    @property
    def rhsm_products(self):
        """List of products/image groups for all the service providers"""
        if self._rhsm_products is None:
            response = self.rhsm_client.rhsm_products().result()
            self._rhsm_products = response.json()["body"]
            prod_names = [
                "%s(%s)" % (p["name"], p["providerShortName"])
                for p in self._rhsm_products
            ]
            LOG.debug(
                "%s Products(AWS provider) in rhsm: %s",
                len(prod_names),
                ", ".join(sorted(prod_names)),
            )
        return self._rhsm_products

    def to_rhsm_product(self, product, image_type):
        """Product info from rhsm for the specified product in metadata"""
        # The rhsm prodcut should always be the product (short) plus
        # "_HOURLY" for hourly type images.
        image_type = image_type.upper()
        aws_provider_name = self.args.aws_provider_name
        if image_type == "HOURLY":
            product = product + "_" + image_type

        LOG.debug(
            "Searching for product %s for provider %s in rhsm",
            product,
            aws_provider_name,
        )
        for rhsm_product in self.rhsm_products:
            if (
                rhsm_product["name"] == product
                and rhsm_product["providerShortName"] == aws_provider_name
            ):
                return rhsm_product

        raise MissingProductError("Product not in rhsm: %s" % product)

    def in_rhsm(self, product, image_type):
        """Checks whether the product is present in rhsm for the provider.
        Returns True if the product is found in rhsm_products else False.
        """
        try:
            self.to_rhsm_product(product, image_type)
        except MissingProductError as er:
            LOG.error(er)
            return False

        return True

    def items_in_metadata_service(self):
        """Checks for all the push_items whether they are in
        rhsm or not.
        Returns false if any of item is missing else true.
        """
        verified = True
        for item in self.ami_push_items:
            if not self.in_rhsm(item.release.product, item.type):
                LOG.error(
                    "Pre-push check in metadata service failed for %s at %s",
                    item.name,
                    item.src,
                )
                attr.evolve(item, state="INVALIDFILE")
                verified = False
        return verified

    def name_from_metadata(self, push_item):
        """
        Constructs an image name from the metadata.
        """
        parts = []
        release = push_item.release

        if release.base_product is not None:
            parts.append(release.base_product)
            if release.base_version is not None:
                parts.append(release.base_version)

        parts.append(release.product)

        # Some attributes should be separated by underscores
        underscore_parts = []

        if release.version is not None:
            underscore_parts.append(release.version)

        underscore_parts.append(push_item.virtualization.upper())

        if release.type is not None:
            underscore_parts.append(release.type.upper())

        parts.append("_".join(underscore_parts))

        parts.append(release.date.strftime("%Y%m%d"))
        parts.append(release.arch)
        parts.append(str(release.respin))
        parts.append(push_item.billing_codes.name)
        parts.append(push_item.volume.upper())

        return "-".join(parts)

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
            "billing_products": push_item.billing_codes.codes,
            "accounts": accounts,
            "sriov_net_support": push_item.sriov_net_support,
            "ena_support": push_item.ena_support or False,
        }
        LOG.debug("%s", publishing_meta_kwargs)
        publish_meta = AWSPublishingMetadata(**publishing_meta_kwargs)

        aws = self.aws_service(region)
        try:
            image = aws.publish(publish_meta)
        except Exception as exc:  # pylint:disable=broad-except
            raise_from(AWSPublishError(exc), exc)

        if ship:
            self.update_rhsm_metadata(image, push_item)

            if image_type == "hourly" and self.args.allow_public_images:
                LOG.info("Releasing hourly image %s publicly", image.id)
                # The "all" group grants access to the general public. This
                # should only be done for hourly images since they are the only
                # type to charge an additional Red Hat fee.
                # http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/sharingamis-intro.html
                publish_meta.groups = ["all"]
                # A repeat call to publish will only update the groups
                try:
                    aws.publish(publish_meta)
                except Exception as exc:  # pylint:disable=broad-except
                    raise_from(AWSPublishError(exc), exc)

        LOG.info("Successfully uploaded %s [%s] [%s]", name, region, image.id)

        return image.id, name

    def update_rhsm_metadata(self, image, push_item):
        """Update rhsm with the uploaded image info. First it creates the region of
        the image assuming it returns OK if the region is present. Then tries to update
        the existing image info. If the image info is not preset, it creates one.
        """
        LOG.info(
            "Creating region %s [%s]", push_item.region, self.args.aws_provider_name
        )
        out = self.rhsm_client.create_region(
            push_item.region, self.args.aws_provider_name
        )

        response = out.result()
        if not response.ok:
            LOG.error(
                "Failed creating region %s for image %s", push_item.region, image.id
            )
            response.raise_for_status()

        LOG.info("Registering image %s with rhsm", image.id)
        image_meta = {
            "image_id": image.id,
            "image_name": image.name,
            "arch": push_item.release.arch,
            "product_name": self.to_rhsm_product(
                push_item.release.product, push_item.type
            )["name"],
            "version": push_item.release.version or None,
            "variant": push_item.release.variant or None,
        }
        LOG.info("Attempting to update the existing image %s in rhsm", image.id)
        LOG.debug("%s", image_meta)
        out = self.rhsm_client.update_image(**image_meta)
        response = out.result()
        if not response.ok:
            LOG.warning(
                "Update to rhsm failed for %s with error code %s. "
                "Image might not be present on rhsm for update.",
                image.id,
                response.status_code,
            )

            LOG.info("Attempting to create new image %s in rhsm", image.id)
            image_meta.update({"region": push_item.region})
            LOG.debug("%s", image_meta)
            out = self.rhsm_client.create_image(**image_meta)
            response = out.result()
            if not response.ok:
                LOG.error(
                    "Failed to create image %s in rhsm with error code %s",
                    image.id,
                    response.status_code,
                )
                LOG.error(response.text)
                response.raise_for_status()
        LOG.info("Successfully registered image %s with rhsm", image.id)

    def region_data(self):
        """Aggregate push_items for each item and region
        for various destinations
        """
        region_data = {}
        for item in self.ami_push_items:
            for _ in item.dest:
                region = item.region
                region_data.setdefault((item, region), []).append({"push_item": item})
        return region_data.values()

    def _push_to_region(self, region_data):
        # sends the push_items for each region to be uploaded in
        # a single thread
        retry_wait = self.args.retry_wait or 30
        max_retries = self.args.max_retries or 4

        for dest_data in region_data:
            push_item = dest_data["push_item"]
            image_id = image_name = None
            retries = max_retries

            while True:
                try:
                    image_id, image_name = self.upload(push_item)
                    state = "PUSHED"
                except (HTTPError, AWSPublishError) as exc:
                    LOG.warning(str(exc))
                    if retries > 0:
                        retries -= 1
                        LOG.info("Retrying upload")
                        time.sleep(retry_wait)
                        continue
                    LOG.error(
                        "Upload failed after %s attempts. Giving up", (max_retries + 1)
                    )
                    state = "NOTPUSHED"
                # break statement is not covered in py38
                # https://github.com/nedbat/coveragepy/issues/772
                break  # pragma: no cover
            dest_data["push_item"] = attr.evolve(push_item, state=state)
            dest_data["state"] = state
            dest_data["image_id"] = image_id
            dest_data["image_name"] = image_name
        return region_data

    def collect_push_result(self, results):
        """Collects the push results and sends its json to the collector"""

        def convert(obj):
            if isinstance(obj, (datetime.datetime, datetime.date)):
                return obj.strftime("%Y%m%d")

        mod_result = []
        for result in results:
            res_dict = attr.asdict(result["push_item"])
            # dict can't be modified during iteration.
            # so iterate over list of keys.
            for key in list(res_dict):
                if res_dict[key] is None:
                    del res_dict[key]
            res_dict["ami"] = result["image_id"]
            res_dict["name"] = result["image_name"]
            mod_result.append(res_dict)

        metadata = json.dumps(mod_result, default=convert, indent=2, sort_keys=True)
        return self.collector.attach_file("images.json", metadata).result()

    def add_args(self):
        super(AmiPush, self).add_args()

        group = self.parser.add_argument_group("AMI Push options")

        group.add_argument(
            "source",
            nargs="+",
            help="source location of the staged AMIs with the source type. "
            "e.g. staged:/path/to/stage/ami or "
            "errata:https://errata.example.com?errata=RHBA-2020:1234",
            action=SplitAndExtend,
            split_on=",",
        )

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
            default={},
        )

        group.add_argument(
            "--allow-public-images",
            help="images are released for general use",
            action="store_true",
        )

        group.add_argument(
            "--aws-provider-name",
            help="AWS provider e.g. AWS, ACN (AWS China), AGOV (AWS US Gov)",
            default="AWS",
        )

        group.add_argument(
            "--retry-wait",
            help="duration to wait in sec before retrying upload",
            type=int,
            default=30,
        )

        group.add_argument(
            "--max-retries",
            help="number of retries on failure to upload",
            type=int,
            default=4,
        )

    def run(self):
        # verify push_items
        if not self.items_in_metadata_service():
            self.fail("Pre-push verification of push items in metadata service failed")

        # split push_items into regions
        region_data = self.region_data()

        # upload
        executor = Executors.thread_pool(
            name="pubtools-ami-push",
            max_workers=min(len(region_data), self._REQUEST_THREADS),
        )
        to_await = []
        result = []
        for data in region_data:
            to_await.append(executor.submit(self._push_to_region, data))

        # waiting for results
        for f_out in to_await:
            result.extend(f_out.result())

        # process result for failures
        failed = False
        for r in result:
            if not r.get("image_id"):
                failed = True

        # send to collector
        LOG.info("Collecting results")
        self.collect_push_result(result)

        if failed:
            self.fail("AMI upload failed")

        LOG.info("AMI upload completed")


def entry_point(cls=AmiPush):
    cls().main()


def doc_parser():
    return AmiPush().parser
