import logging
import threading

from pubtools.pluggy import pm
from pubtools._ami.rhsm import RHSMClient
from pubtools._ami.arguments import from_environ
from .base import Service

LOG = logging.getLogger("pubtools.ami")


class RHSMClientService(Service):
    """A service providing RHSM client.

    A client will be available only when RHSM url is  provided.
    """

    def __init__(self, *args, **kwargs):
        self._lock = threading.Lock()
        self._rhsm_instance = None
        super(RHSMClientService, self).__init__(*args, **kwargs)

    def add_service_args(self, parser):
        super(RHSMClientService, self).add_service_args(parser)

        group = parser.add_argument_group("RHSM service")

        group.add_argument("--rhsm-url", help="Base URL of the RHSM API")
        group.add_argument(
            "--rhsm-cert",
            help="RHSM API certificate path (or set RHSM_CERT environment variable)",
            type=from_environ("RHSM_CERT"),
        )
        group.add_argument(
            "--rhsm-key",
            help="RHSM API key path (or set RHSM_KEY environment variable)",
            type=from_environ("RHSM_KEY"),
        )

    @property
    def rhsm_client(self):
        """RHSM client used for AMI related info on RHSM.

        Error will be raised if the URL is not provided in the CLI.
        """
        with self._lock:
            if not self._rhsm_instance:
                self._rhsm_instance = self._get_rhsm_instance()
        return self._rhsm_instance

    def _get_rhsm_instance(self):
        rhsm_url = self._service_args.rhsm_url
        rhsm_cert = self._service_args.rhsm_cert
        rhsm_key = self._service_args.rhsm_key
        if not rhsm_url:
            raise Exception("RHSM URL not provided for the RHSM client")

        result = pm.hook.get_cert_key_paths(  # pylint: disable=no-member
            server_url=rhsm_url
        )
        default_cert, default_key = result if result else (None, None)
        cert = rhsm_cert or default_cert, rhsm_key or default_key
        return RHSMClient(rhsm_url, cert=cert)
