push
====

.. argparse::
   :module: pubtools.adc.tasks.push
   :func: doc_parser
   :prog: pubtools-adc-push

Example
.......

A typical invocation of push would look like this:

.. code-block::

  pubtools-adc-push \
    --aws-provider-name awstest \
    --accounts '{"region-1": {"access-r": "secret-r"},
                 "default": {"access-1": "secret-1"}}' \
    --aws-access-id access_id \
    --aws-secret-key secret_key \
    staged:/stage/ami/root

All the AMIs in the given source path will be uploaded
to AWS for the accounts of the region the image is pushed to
or the default accounts.


Example: ship images
....................

To the ship the images to be available to users, --ship option
should be used.

.. code-block::

  pubtools-adc-push \
    --aws-provider-name awstest \
    --accounts '{"region-1": {"access-r": "secret-r"},
                 "default": {"access-1": "secret-1"}}' \
    --aws-access-id access_id \
    --aws-secret-key secret_key \
    --ship \
    staged:/stage/ami/root


Shipping the images to general public i.e. ones that are available
to all like hourly images which can be used by general users for a
fee requires using --allow-public-images along with the above options.

.. code-block::

  pubtools-adc-push \
    --aws-provider-name awstest \
    --accounts '{"region-1": {"access-r": "secret-r"},
                 "default": {"access-1": "secret-1"}}' \
    --aws-access-id access_id \
    --aws-secret-key secret_key \
    --ship \
    --allow-public-image \
    staged:/stage/ami/root


Example: modify retry
.....................

Uploads might fail on the first try for any reason. It gets retired by default
for 4 times after every 30 seconds. These defaults can be modified as:

.. code-block::

  pubtools-adc-push \
    --aws-provider-name awstest \
    --accounts '{"region-1": {"access-r": "secret-r"},
                 "default": {"access-1": "secret-1"}}' \
    --aws-access-id access_id \
    --aws-secret-key secret_key \
    --max-retires 2 \
    --retry-wait 10 \
    staged:/stage/ami/root
