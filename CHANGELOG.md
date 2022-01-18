# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- n/a

## 1.0.0 - 2022-01-18

### Fixed

- Fixed response processing so push fails now if any of the pushitem fails

### Changed

- Modified images.json format updating some field names and removed null values


## 0.1.2 - 2021-12-14

### Fixed

- Fixed fetching default certs from hook method get_cert_key_paths
- Fixed incorrect product_name in RHSM's create and update image api

## 0.1.1 - 2021-11-30

### Fixed

- Added missing dependencies `attrs` and `six`

## 0.1.0 - 2021-11-17

- Initial release to PyPI

[Unreleased]: https://github.com/release-engineering/pubtools-ami/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/release-engineering/pubtools-ami/compare/v1.0.0...0.1.2
[0.1.2]: https://github.com/release-engineering/pubtools-ami/compare/v0.1.2...0.1.1
[0.1.1]: https://github.com/release-engineering/pubtools-ami/compare/v0.1.1...0.1.0

