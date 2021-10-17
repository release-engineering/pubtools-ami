import threading
import os
import logging
from datetime import datetime
from six.moves.urllib.parse import urljoin

import requests
from more_executors import Executors
from more_executors.futures import f_map

LOG = logging.getLogger("pubtools.ami")


class RHSMClient(object):
    # Client for RHSM updates.

    _REQUEST_THREADS = int(os.environ.get("RHSM_REQUEST_THREADS", "4"))

    def __init__(self, url, max_retry_sleep=None, **kwargs):
        """Create a new RHSM client.

        Arguments:
            ulr(str)
                Base URL of the RHSM API.
            max_retry_sleep (float)
                Max number of seconds to sleep between retries.
                Mainly provided so that tests can reduce the time needed to retry.
            kwargs
                Remaining arguments are used to initialize the requests.Session()
                used within this class (e.g. "verify", "auth").
        """
        self._url = url
        self._tls = threading.local()

        retry_args = {}
        if max_retry_sleep:
            retry_args["max_sleep"] = max_retry_sleep

        self._session_attrs = kwargs
        self._executor = Executors.thread_pool(
            name="rhsm-client", max_workers=self._REQUEST_THREADS
        ).with_retry(**retry_args)

    @staticmethod
    def _check_http_response(response):
        response.raise_for_status()
        return response

    @property
    def _session(self):
        if not hasattr(self._tls, "session"):
            self._tls.session = requests.Session()
            for (key, value) in self._session_attrs.items():
                setattr(self._tls.session, key, value)
        return self._tls.session

    def _on_failure(self, exception):
        LOG.error("Failed to process request to RHSM with exception %s", exception)
        raise exception

    def _get(self, *args, **kwargs):
        return self._session.get(*args, **kwargs)

    def _send(self, *args, **kwargs):
        return self._session.send(*args, **kwargs)

    def rhsm_products(self):
        url = urljoin(
            self._url,
            "/v1/internal/cloud_access_providers/amazon" + "/provider_image_groups",
        )
        LOG.debug("Fetching product from %s", url)

        out = self._executor.submit(self._get, url)
        out = f_map(out, fn=self._check_http_response, error_fn=self._on_failure)

        return out

    def create_region(self, region, aws_provider_name):
        url = urljoin(self._url, "v1/internal/cloud_access_providers/amazon/regions")

        rhsm_region = {"regionID": region, "providerShortname": aws_provider_name}
        req = requests.Request("POST", url, json=rhsm_region)
        prepped_req = self._session.prepare_request(req)

        out = self._executor.submit(self._send, prepped_req)
        out = f_map(out, error_fn=self._on_failure)

        return out

    def update_image(
        self, image_id, image_name, arch, product_name, version=None, variant=None
    ):
        url = urljoin(self._url, "/v1/internal/cloud_access_providers/amazon/amis")

        now = datetime.utcnow().replace(microsecond=0).isoformat()
        rhsm_image = {
            "amiID": image_id,
            "arch": arch.lower(),
            "product": product_name,
            "version": version or "none",
            "variant": variant or "none",
            "description": "Released %s on %s" % (image_name, now),
            "status": "VISIBLE",
        }
        req = requests.Request("PUT", url, json=rhsm_image)
        prepped_req = self._session.prepare_request(req)

        out = self._executor.submit(self._send, prepped_req)
        out = f_map(out, error_fn=self._on_failure)

        return out

    def create_image(
        self,
        image_id,
        image_name,
        arch,
        product_name,
        region,
        version=None,
        variant=None,
    ):
        url = urljoin(self._url, "/v1/internal/cloud_access_providers/amazon/amis")

        now = datetime.utcnow().replace(microsecond=0).isoformat()
        rhsm_image = {
            "amiID": image_id,
            "region": region,
            "arch": arch.lower(),
            "product": product_name,
            "version": version or "none",
            "variant": variant or "none",
            "description": "Released %s on %s" % (image_name, now),
            "status": "VISIBLE",
        }
        req = requests.Request("POST", url, json=rhsm_image)
        prepped_req = self._session.prepare_request(req)

        out = self._executor.submit(self._send, prepped_req)
        out = f_map(out, error_fn=self._on_failure)

        return out
