delete
======

.. argparse::
   :module: pubtools._ami.tasks.delete
   :func: doc_parser
   :prog: pubtools-ami-delete


Example
.......

A typical invocation of delete would look like this:

.. code-block::

  pubtools-ami-delete \
    --rhsm-url https://rhsm.example.com \
    --aws-provider-name awstest \
    --aws-access-id access_id \
    --aws-secret-key secret_key \
    pub:https://pub.example.com?task_id=123456

All the AMIs in the given source path will be made invisible
in RHSM and then deleted on AWS with related snapshots.


Example: keep snapshots
.......................

Running delete while keeping snapshots untouched.

.. code-block::

  pubtools-ami-delete \
    --rhsm-url https://rhsm.example.com \
    --keep-snapshot \
    --aws-provider-name awstest \
    --aws-access-id access_id \
    --aws-secret-key secret_key \
    pub:https://pub.example.com?task_id=123456


Example: dry-run
................

Running dry-run delete - no destructive actions happen,
expected actions are logged.

.. code-block::

  pubtools-ami-delete \
    --rhsm-url https://rhsm.example.com \
    --dry-run \
    --aws-provider-name awstest \
    --aws-access-id access_id \
    --aws-secret-key secret_key \
    pub:https://pub.example.com?task_id=123456

