import logging
import threading
from functools import partial
from cloudimg.aws import AWSService
from pubtools._ami.arguments import from_environ
from .base import Service

LOG = logging.getLogger("pubtools.ami")


class AWSPublishService(Service):
    """A service providing AWS service for publishing AMI.

    The service client is returned when the access-id and
    secret-key is provided.
    """

    def __init__(self, *args, **kwargs):
        self._instance = None
        self._lock = threading.Lock()
        super(AWSPublishService, self).__init__(*args, **kwargs)

    def add_service_args(self, parser):
        super(AWSPublishService, self).add_service_args(parser)

        group = parser.add_argument_group("AWS Service")

        group.add_argument(
            "--aws-access-id",
            help="The AWS Service ID or Login to access the service "
            "(or set AWS_ACCESS_ID environment variable)",
            default="",
            type=from_environ("AWS_ACCESS_ID"),
        )

        group.add_argument(
            "--aws-secret-key",
            help="The AWS Service key to access the service "
            "(or set AWS_SECRET_KEY environment variable)",
            default="",
            type=from_environ("AWS_SECRET_KEY"),
        )

    def aws_service(self, region):
        """An partial instance of AWS Service to publish AMI"""
        with self._lock:
            if not self._instance:
                self._instance = self._get_instance()
        return self._instance(region=region)

    def _get_instance(self):
        access_id = self._service_args.aws_access_id
        secret_key = self._service_args.aws_secret_key
        if not (access_id and secret_key):
            raise Exception("Access ID or Secret Key not provided for AWS Service")

        return partial(AWSService, access_id, secret_key)
