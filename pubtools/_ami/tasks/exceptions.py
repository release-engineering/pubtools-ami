class AWSPublishError(Exception):
    """Exception class for AWS publish errors"""


class AWSDeleteError(Exception):
    """Exception class for AWS delete errors"""


class MissingProductError(Exception):
    """Exception class for products missing in the metadata service"""
