# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- n/a

## [2.4.2] - 2024-07-19

- Fixed source code structure

## [2.4.1] - 2023-11-29

- Fixed setting `boot_mode` value in images.json file

## [2.4.0] - 2023-09-15

- Added support for `boot_mode` option in publishing metadata
- Improved logging and retries handling in `pubtools-ami-push`

## [2.3.1] - 2023-09-11

- Updated the dependencies

## [2.3.0] - 2023-08-21

### Added

- Support for `uefi_support` flag on AMI push item.

## [2.2.0] - 2023-04-27

### Added

- Introduced new command `pubtools-ami-delete`

## [2.1.0] - 2023-03-14

### Added

- Support for `public_image` flag on AMI push item.

### Fixed

- Application of environment settings for Prepared requests.

## [2.0.0] - 2023-01-17

### Changed

- `pubtools-ami-push`: `--snapshot-account-ids` now uses a JSON format argument.

## [1.2.1] - 2022-07-13

### Changed

- `pubtools-ami-push`: Removed defaults for `--snapshot-account-ids`

## [1.2.0] - 2022-06-22

### Added

- `pubtools-ami-push`: `--snapshot-account-ids` now accepts comma-separated values.

## [1.1.0] - 2022-05-13

### Added

- `pubtools-ami-push`: introduced the `--snapshot-account-ids` argument.

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

[Unreleased]: https://github.com/release-engineering/pubtools-ami/compare/v2.4.2...HEAD
[2.4.2]: https://github.com/release-engineering/pubtools-ami/compare/v2.4.1...v2.4.2
[2.4.1]: https://github.com/release-engineering/pubtools-ami/compare/v2.4.0...v2.4.1
[2.4.0]: https://github.com/release-engineering/pubtools-ami/compare/v2.3.1...v2.4.0
[2.3.1]: https://github.com/release-engineering/pubtools-ami/compare/v2.3.0...v2.3.1
[2.3.0]: https://github.com/release-engineering/pubtools-ami/compare/v2.2.0...v2.3.0
[2.2.0]: https://github.com/release-engineering/pubtools-ami/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/release-engineering/pubtools-ami/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/release-engineering/pubtools-ami/compare/v1.2.1...v2.0.0
[1.2.1]: https://github.com/release-engineering/pubtools-ami/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/release-engineering/pubtools-ami/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/release-engineering/pubtools-ami/compare/v1.0.0...v1.1.0
