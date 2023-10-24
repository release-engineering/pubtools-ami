import logging
import os
from concurrent.futures import as_completed

import attr
from cloudimg.aws import AWSDeleteMetadata
from more_executors import Executors, ExceptionRetryPolicy

from ..arguments import SplitAndExtend
from ..services import AWSPublishService, CollectorService, RHSMClientService
from ..task import AmiTask
from .base import AmiBase
from .exceptions import AWSDeleteError


LOG = logging.getLogger("pubtools.ami")

step = AmiTask.step


class AmiDelete(AmiBase, RHSMClientService, AWSPublishService, CollectorService):
    """Deletes one or more Amazon Machine Images on AWS from specified sources.

    This command gets the AMIs from specified source, checks the image presence
    in metadata service (e.g. RHSM) and makes image invisible in this service.
    This is followed by deletion of image and related snapshot in AWS.
    """

    _REQUEST_THREADS = int(os.environ.get("AMI_DELETE_REQUEST_THREADS", "5"))

    def __init__(self, *args, **kwargs):
        self._rhsm_products = None
        super(AmiDelete, self).__init__(*args, **kwargs)

    def add_args(self):
        super(AmiDelete, self).add_args()

        group = self.parser.add_argument_group("AMI delete options")

        group.add_argument(
            "--keep-snapshot",
            help="Do not delete snapshot from AWS",
            action="store_true",
        )

        group.add_argument(
            "--dry-run",
            help="Skip destructive actions on rhsm or AWS",
            action="store_true",
        )

        group.add_argument(
            "--limit",
            nargs="+",
            help="Only remove the specified AMIs by AMI image id",
            action=SplitAndExtend,
            split_on=",",
        )

    def _delete_in_region(self, region_data):
        for dest_data in region_data:
            push_item = dest_data["push_item"]
            state = push_item.state
            if self.args.dry_run:
                LOG.info(
                    "Would have deleted image %s and related snapshot in AWS (%s)",
                    push_item.image_id,
                    self.args.aws_provider_name,
                )
                continue

            image_id, snapshot_id = self._delete(push_item)
            if image_id or snapshot_id:
                state = "DELETED"
            else:
                state = "MISSING"

            dest_data["push_item"] = attr.evolve(push_item, state=state)
            dest_data["state"] = state
            dest_data["image_id"] = image_id
            dest_data["snapshot_id"] = snapshot_id

        return region_data

    def _delete(self, push_item):
        """
        Request deletion on AWS for single AMI push_item.

        Deletion of both image and related snapshot is attempted
        and if succesfull, ids of removed image and snapshot are returned.

        Deletion of snapshot can be skipped by providing --keep-snapshot arg.
        """
        region = push_item.region
        name = self.name_from_metadata(push_item)
        LOG.info(
            "Attempting to delete image %s and related snapshot on AWS (%s)",
            name,
            self.args.aws_provider_name,
        )
        delete_meta_kwargs = {
            "image_id": push_item.image_id,
            "image_name": name,
            # currently using getattr for snapshot_id/name because
            # snapshot related fields are not available in pushitem
            "snapshot_id": getattr(push_item, "snapshot_id", None),
            "snapshot_name": getattr(push_item, "snapshot_name", name),
            "skip_snapshot": self.args.keep_snapshot,
        }
        metadata = AWSDeleteMetadata(**delete_meta_kwargs)

        aws = self.aws_service(region)
        try:
            out = aws.delete(metadata)
        except Exception as exc:  # pylint:disable=broad-except
            LOG.error("AWS delete failed for AMI %s", push_item.image_id)
            raise AWSDeleteError(exc) from exc

        deleted_image_id, deleted_snapshot_id = out
        if deleted_image_id:
            LOG.info(
                "Successfully deleted image: %s [%s] [%s]",
                name,
                region,
                deleted_image_id,
            )
        if deleted_snapshot_id:
            LOG.info(
                "Successfully deleted snapshot: %s [%s] [%s]",
                name,
                region,
                deleted_snapshot_id,
            )

        return deleted_image_id, deleted_snapshot_id

    @step("Prepare data")
    def limit_push_items(self):
        """
        If --limit arg is provided, attempt deletion only
        for specified AMIs.
        """
        filtered_push_items = []
        if self.args.limit:
            for item in self.ami_push_items:
                if item.image_id in self.args.limit:
                    filtered_push_items.append(item)

            self.ami_push_items = filtered_push_items

    @step("Update RHSM metadata")
    def update_rhsm_metadata(self):
        """
        Sets images as 'INVISIBLE' in RHSM metadata service, if the image
        is present. Missing image isn't counted as a fatal error.
        """
        rhsm_image_ids = self.rhsm_client.list_image_ids()

        for item in self.ami_push_items:
            if item.image_id not in rhsm_image_ids:
                LOG.warning(
                    "AMI image: %s not found, skipping update in rhsm.", item.image_id
                )
            else:
                image_meta = {
                    "image_id": item.image_id,
                    "image_name": item.name,
                    "arch": item.release.arch,
                    "product_name": self.to_rhsm_product(
                        item.release.product, item.type
                    )["name"],
                    "version": item.release.version or None,
                    "variant": item.release.variant or None,
                    "status": "invisible",
                }
                if self.args.dry_run:
                    LOG.info("Would have updated image %s in rhsm", item.image_id)
                    continue

                LOG.info(
                    "Attempting to update the existing image %s in rhsm", item.image_id
                )
                out = self.rhsm_client.update_image(**image_meta)
                resp = out.result()
                if not resp.ok:
                    LOG.error("Failed updating image %s", item.image_id)
                    resp.raise_for_status()

                LOG.info("Existing image %s succesfully updated in rhsm", item.image_id)

    @step("Delete AWS data")
    def do_delete(self):
        # split push_items into regions
        region_data = self.region_data()
        to_await = []
        result = []
        failure = False

        # run the actual delete in aws
        with Executors.thread_pool(
            name="pubtools-ami-delete",
            max_workers=min(len(region_data), self._REQUEST_THREADS),
        ).with_retry(
            logger=LOG,
            retry_policy=ExceptionRetryPolicy(
                sleep=self.args.retry_wait,
                max_attempts=self.args.max_retries,
            ),
        ) as executor:
            for data in region_data:
                to_await.append(executor.submit(self._delete_in_region, data))

            # waiting for results
            for f_out in as_completed(to_await):
                try:
                    result.extend(f_out.result())
                except Exception as exc:  # pylint:disable=broad-except
                    LOG.exception("AMI delete failed", exc_info=exc)
                    failure = True

        return failure, result

    def run(self):
        self.limit_push_items()
        if not self.ami_push_items:
            LOG.info("No AMI image selected for deletion")
            return

        # set images in rhsm as invisible
        self.update_rhsm_metadata()

        # perform deletion on AWS
        failure, result = self.do_delete()

        if self.args.dry_run:
            LOG.info("AMI delete dry-run completed")
        else:
            # send to collector
            LOG.info("Collecting results")
            self.collect_push_result(result)

            if failure:
                self.fail("AMI delete finished with failure")

            LOG.info("AMI delete completed")


def entry_point(cls=AmiDelete):
    cls().main()


def doc_parser():
    return AmiDelete().parser
