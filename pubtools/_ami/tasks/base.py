import datetime
import json
import logging
import sys
from concurrent.futures import wait

import attr
from pushsource import Source, AmiPushItem, BootMode

from ..arguments import SplitAndExtend
from ..services import RHSMClientService, AWSPublishService, CollectorService
from ..task import AmiTask
from .exceptions import MissingProductError


LOG = logging.getLogger("pubtools.ami")


class AmiBase(AmiTask, RHSMClientService, AWSPublishService, CollectorService):
    """
    Base class for AMI specific tasks that holds common logic.
    """

    def __init__(self, *args, **kwargs):
        self._ami_push_items = None
        self._rhsm_products = None
        super(AmiBase, self).__init__(*args, **kwargs)

    @property
    def ami_push_items(self):
        if self._ami_push_items is None:
            self._ami_push_items = self._get_push_items()
        return self._ami_push_items or None

    @ami_push_items.setter
    def ami_push_items(self, values):
        self._ami_push_items = values

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

    def add_args(self):
        super(AmiBase, self).add_args()

        group = self.parser.add_argument_group("AMI common options")
        group.add_argument(
            "source",
            nargs="+",
            help="source location of the staged AMIs with the source type. "
            "e.g. staged:/path/to/stage/ami or "
            "errata:https://errata.example.com?errata=RHBA-2020:1234 or "
            "pub:https://pub.example.com?task_id=125222",
            action=SplitAndExtend,
            split_on=",",
        )

        group.add_argument(
            "--aws-provider-name",
            help="AWS provider e.g. AWS, ACN (AWS China), AGOV (AWS US Gov)",
            default="AWS",
        )

        group.add_argument(
            "--retry-wait",
            help="duration to wait in sec before retrying action on AWS",
            type=int,
            default=30,
        )

        group.add_argument(
            "--max-retries",
            help="number of retries on failure with action on AWS",
            type=int,
            default=4,
        )

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

    def collect_push_result(self, results):
        """Collects the push results and sends its json to the collector"""
        RESULT_KEY_MAPPING = {
            "ami": "image_id",
            "name": "image_name",
            "snapshot_id": "snapshot_id",
        }

        def convert(obj):
            if isinstance(obj, (datetime.datetime, datetime.date)):
                return obj.strftime("%Y%m%d")
            if isinstance(obj, BootMode):
                return obj.value

        mod_result = []
        push_items = []
        for result in results:
            push_items.append(self._pushitem_for_ami(result["push_item"]))
            res_dict = attr.asdict(result["push_item"])
            # dict can't be modified during iteration.
            # so iterate over list of keys.
            for key in list(res_dict):
                if res_dict[key] is None:
                    del res_dict[key]

            for k, v in RESULT_KEY_MAPPING.items():
                if v in result:
                    res_dict[k] = result[v]

            mod_result.append(res_dict)

        metadata = json.dumps(mod_result, default=convert, indent=2, sort_keys=True)
        wait(
            [
                self.collector.attach_file("images.json", metadata),
                self.collector.update_push_items(push_items),
            ]
        )

    def _pushitem_for_ami(self, ami_item):
        return {
            "filename": f"{ami_item.image_id}",
            "state": f"{ami_item.state}",
        }

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
