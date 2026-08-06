# Official Firefox and NVDA Installers Design

## Goal

Change the Windows accessibility AMI provisioning flow so that every build
installs the current stable releases of Firefox and NVDA directly from their
official publishers instead of obtaining either package through Chocolatey.

Firefox must be the Traditional Chinese (`zh-TW`) 64-bit stable release. NVDA
must be the current stable release; beta and release-candidate builds are not
eligible.

## Current State

`scripts/windows-a11y/install-software.ps1` currently bootstraps Chocolatey and
runs `choco upgrade` for the `firefox` and `nvda` packages. Chrome already uses
an official evergreen MSI and validates its Authenticode publisher. The AMI
workflow reads `VERSION_FIREFOX` and `VERSION_NVDA` from the script output and
writes those values into AMI tags.

Although this environment is commonly described as the Windows 11 AMI, the
current workflow builds from AWS's Traditional Chinese Windows Server 2025
base image. This change does not alter the base image or any other provisioning
behavior.

## Selected Approach

Use each publisher's stable, evergreen download surface at AMI build time:

- Firefox: request Mozilla's official redirect endpoint with
  `product=firefox-latest-ssl`, `os=win64`, and `lang=zh-TW`.
- NVDA: read the official `https://download.nvaccess.org/releases/stable/`
  directory and select the installer whose filename matches
  `nvda_<numeric-stable-version>.exe`.

The NVDA filename rule permits numeric releases such as `2026.1` and
`2026.1.1`. It rejects filenames containing `alpha`, `beta`, `rc`, or any other
non-numeric version suffix. Resolution must fail if there is not exactly one
matching installer, rather than guessing among ambiguous results.

This approach is preferred over parsing product marketing pages because the
evergreen endpoint and stable release directory are narrower, machine-oriented
publisher surfaces. Pinning URLs in the repository was rejected because it
would require manual updates and would not meet the requirement to install the
latest stable release on every AMI build.

## Installation Flow

The PowerShell script will define focused helpers for downloading with bounded
retries, validating Authenticode signatures, resolving the NVDA stable
installer, and running installers while checking their exit codes. The script
will retain a single orchestration entry point so its current SSM invocation
does not change.

For each product:

1. Create or reuse the existing temporary installer directory.
2. Resolve the official stable download URL.
3. Download the installer over HTTPS, trying at most three times and waiting
   15 seconds and then 30 seconds before the two retries.
4. Require an Authenticode status of `Valid` and an expected publisher:
   `Mozilla Corporation` for Firefox and `NV Access Limited` for NVDA.
5. Run the installer silently and wait for completion: Firefox with `/S` and
   NVDA with `--install-silent`.
6. Accept exit code `0` from both executable installers; otherwise fail the
   provisioning command with the exit code and available diagnostic context.
7. Remove the downloaded installer after successful installation.

Firefox will use Mozilla's supported silent-install switch. NVDA will use its
silent install command and install system-wide at the current 64-bit executable
location expected by `verify-environment.ps1`. The legacy x86 location remains
an explicit fallback for existing installations.

There is no Chocolatey fallback. An unavailable official endpoint, malformed
stable listing, invalid signature, unexpected publisher, or failed installer
must stop the AMI build so an unverified or stale package is never baked into
the image.

## Compatibility and Outputs

The existing executable checks remain authoritative:

- Firefox: `C:\Program Files\Mozilla Firefox\firefox.exe`
- NVDA primary: `C:\Program Files\NVDA\nvda.exe`
- NVDA legacy fallback: `C:\Program Files (x86)\NVDA\nvda.exe`

After installation, the script will continue reading product/file version
metadata from those executables and emitting the existing lines:

```text
VERSION_FIREFOX=<installed version>
VERSION_NVDA=<installed version>
```

Consequently, `.github/workflows/build-windows-a11y-ami.yml`, its output
parsing, AMI tags, and `verify-environment.ps1` require no interface changes.
The now-unused Chocolatey bootstrap and package-upgrade helper will be removed.

## Error Handling and Logging

Messages will identify the product, operation, attempt number, and terminal
failure without printing credentials or unrelated environment data. Retry is
limited to transient download failures. Signature failures, ambiguous NVDA
listings, and installer failures are deterministic and will fail immediately.

Installer processes will be awaited before executable/version checks run, so
the existing verification cannot race an installation still in progress.

## Testing

Pester tests in
`scripts/windows-a11y/tests/install-software.Tests.ps1` will dot-source the
script without executing its orchestration entry point and cover the following
observable behavior:

- the Firefox request uses Mozilla's official latest-stable endpoint with
  `win64` and `zh-TW`;
- NVDA resolution accepts numeric stable filenames, including patch releases;
- NVDA resolution rejects beta and release-candidate filenames;
- zero or multiple eligible NVDA installers produce a clear failure;
- signature validation accepts only `Valid` signatures from the configured
  publisher and rejects invalid or unexpected signatures;
- installer exit-code validation rejects failure codes;
- the public `VERSION_FIREFOX` and `VERSION_NVDA` output contract remains
  unchanged.

The implementation will follow a red-green cycle for each behavior. Final
verification will include the focused PowerShell tests, PowerShell syntax
validation, and inspection of the resulting diff for accidental workflow or
Chocolatey dependencies.

## Out of Scope

- Changing the Windows base AMI or AWS infrastructure.
- Changing Chrome installation behavior.
- Installing Firefox ESR, Beta, Developer Edition, or Nightly.
- Installing NVDA alpha, beta, or release-candidate builds.
- Adding a third-party package-manager fallback.
