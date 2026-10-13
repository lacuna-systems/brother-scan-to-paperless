# brother-scan-to-paperless

A lightweight daemon that turns a Brother network scanner's Scan button into a
one-press pipeline to [Paperless-ngx](https://docs.paperless-ngx.com/).

Press Scan on the printer and the document lands in Paperless.

> This is a personal project tested with a limited set of hardware. Check your
> model's Brother Linux driver before deploying it, and test the complete scan
> workflow in your environment.

## Why this exists

Brother provides `brscan-skey` as its official Linux scan-button tool, but it
can be unreliable in headless and containerized environments. This daemon
replaces the scan-key process with a small Python service that:

- automatically registers scan profiles over Phoenix HTTPS on newer printers
  or SNMP on older printers;
- listens for button events on UDP port `54925`;
- uses `scanimage` batch mode to pull every page in the scanner's current job;
  and
- either writes the result into Paperless-ngx's consume directory or uploads
  it through the Paperless API.

## Choose a scanner backend

Brother models use either `brscan4` or `brscan5`. `brscan5` is not an upgrade
for `brscan4` models. Find your model on
[Brother Support](https://support.brother.com/) and use the scanner driver
generation listed on its Linux download page.

This repository builds two image targets:

| Published image | SANE backend | Device configuration |
|---|---|---|
| `ghcr.io/zshel/brother-scan-to-paperless:brscan4` | `brscan4` 0.4.11-1 | `brother4:net1;dev0` |
| `ghcr.io/zshel/brother-scan-to-paperless:brscan5` | `brscan5` 1.7.0-0 | `brother5:net1;dev0` |

Brother currently publishes its Debian packages for x86 only. All Docker
images published by this project are built for `linux/amd64`.

## Docker

Docker is the recommended installation method. The images include Python,
SANE, SNMP and TIFF tools, this daemon, and the selected scanner backend. They
are for static-IP network scanners; USB scanners are not supported by this
container setup.

### Requirements

- Linux with Docker Engine and Docker Compose
- an `amd64` host, or an environment able to run `linux/amd64` images
- a Brother network scanner with a static or reserved IP address and a
  compatible `brscan4` or `brscan5` backend
- the Docker host's LAN IP address, reachable from the printer
- either a writable Paperless consume directory or a Paperless URL and API
  token

No privileged mode or host device mapping is required for a network scanner.

### Published images

After every successful push to `main`, CI publishes these moving tags:

```bash
docker pull ghcr.io/zshel/brother-scan-to-paperless:brscan4
docker pull ghcr.io/zshel/brother-scan-to-paperless:brscan5
```

A Git tag such as `v1.2.3` also publishes `brscan4-1.2.3`, `brscan4-1.2`,
`brscan5-1.2.3`, and `brscan5-1.2`. Every published build also receives a
backend-specific `brscanN-sha-<commit>` tag. There is no unqualified `latest`
tag because the Brother backends are not interchangeable. For a reproducible
deployment, set `BRSCAN4_IMAGE` or `BRSCAN5_IMAGE` in `.env` to a full
versioned tag or image digest.

The first workflow publication creates a private GHCR package by default for a
personal account. The repository owner must change the
[package visibility](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility)
to public once to allow anonymous pulls. Until then, authenticate with
`docker login ghcr.io` before pulling.

### Run with Compose

1. Create the runtime environment file:

   ```bash
   install -m 600 .env.example .env
   ```

2. Edit `.env`:

   ```dotenv
   PRINTER_IP=192.168.1.50
   HOST_IP=192.168.1.10
   SCANNER_MODEL=DCP-L2540DW
   DELIVERY_MODE=consume
   COMPOSE_FILE=compose.yaml:compose.consume.yaml
   PAPERLESS_CONSUME_DIR=/srv/paperless/consume
   ```

   `SCANNER_MODEL` must exactly match the model name accepted by Brother's
   driver. `HOST_IP` is the LAN address the printer can use to reach this
   machine, not a Docker bridge address. Keep `.env` readable only by its owner
   because API mode can store a Paperless token there. Create the consume
   directory before starting Compose so Docker does not create it as `root`.
   The Compose override applies the shared SELinux `z` label and grants only
   `DAC_OVERRIDE`, allowing the scanner container to write a directory owned by
   the Paperless user while all other Linux capabilities remain dropped.

3. Pull and start exactly one backend profile:

   ```bash
   # For a brscan4 model
   docker compose --profile brscan4 pull
   docker compose --profile brscan4 up --detach --no-build

   # For a brscan5 model
   docker compose --profile brscan5 pull
   docker compose --profile brscan5 up --detach --no-build
   ```

   To build the selected target from the local checkout instead, skip `pull`
   and replace `--no-build` with `--build` in the corresponding `up` command.

4. Follow the logs:

   ```bash
   docker compose --profile brscan4 logs --follow
   ```

The Python command removes and re-adds the Brother SANE network registration
when `run` or `test` starts, then launches the requested operation. In consume
mode, scans are written to the bind-mounted `/consume` directory. Startup fails
if `PAPERLESS_CONSUME_DIR` is empty, which prevents scans from being left in an
unshared container volume by mistake.

Stop the selected profile with:

```bash
docker compose --profile brscan4 down
```

### Upload through the Paperless API

API mode removes the requirement for a directory shared with Paperless. Create
an API token from **My Profile** in Paperless, then configure `.env`:

```dotenv
PRINTER_IP=192.168.1.50
HOST_IP=192.168.1.10
SCANNER_MODEL=DCP-L2540DW
DELIVERY_MODE=api
COMPOSE_FILE=compose.yaml
PAPERLESS_CONSUME_DIR=
PAPERLESS_URL=https://paperless.example.com
PAPERLESS_API_TOKEN=replace-with-a-paperless-token
PAPERLESS_API_TIMEOUT=60
```

`PAPERLESS_URL` is the Paperless base URL, including any reverse-proxy path,
but not `/api`. The token user needs the `documents.add_document` permission.
Start the selected backend profile with the same Compose commands shown above.
HTTPS is required by default. For an isolated development network only, an
`http://` URL also requires `PAPERLESS_ALLOW_INSECURE_HTTP=true`; this exposes
both the token and document contents in plaintext.

The scan is written to the persistent `brother-scan-staging` Docker volume
before upload. Paperless returns a task identifier after accepting the file;
this means consumption was queued, not that OCR has finished. The daemon logs
that task identifier and removes its staged copy only after acceptance.

Upload failures are not retried automatically because a timeout can occur
after Paperless has already accepted the document. The completed scan remains
under `/staging` for manual recovery. To inspect and copy retained scans:

```bash
docker compose --profile brscan4 exec brscan4 find /staging -type f
docker compose --profile brscan4 cp \
  brscan4:/staging/scan_YYYYMMDD_HHMMSS_microseconds_id.tiff ./
```

Files ending in `.partial` are interrupted or incomplete captures and must not
be submitted. A rare local cleanup failure after API acceptance is marked with
`.accepted`; Paperless has already queued that file, so do not resubmit it.

To tag every uploaded scan, set `PAPERLESS_TAGS` to a comma-separated list of
Paperless tag IDs, such as `PAPERLESS_TAGS=3,14`. The upload API accepts IDs,
not names; a tag's ID is shown in the URL when editing it in Paperless, or by
`GET /api/tags/`. Tags are only supported in API mode. In consume mode, use
Paperless's own consumer settings or workflows instead.

For deployments that inject secrets from files, set
`PAPERLESS_API_TOKEN_FILE` to a readable file inside the container instead of
setting `PAPERLESS_API_TOKEN`. A token file takes precedence when both are
configured. Mount that file separately into the container; a host path is not
made available merely by putting it in `.env`.

### Build the images locally

```bash
docker build --platform linux/amd64 --target brscan4 \
  --tag brother-scan-to-paperless:brscan4 .

docker build --platform linux/amd64 --target brscan5 \
  --tag brother-scan-to-paperless:brscan5 .
```

The targets download the pinned official Brother packages and verify their
SHA-256 checksums before installing them.

### Run without Compose

This example uses the `brscan4` image:

```bash
docker run --detach \
  --name brother-scan-to-paperless \
  --restart unless-stopped \
  --network host \
  --cap-drop ALL \
  --cap-add DAC_OVERRIDE \
  --security-opt no-new-privileges:true \
  --env PRINTER_IP=192.168.1.50 \
  --env HOST_IP=192.168.1.10 \
  --env SCANNER_MODEL=DCP-L2540DW \
  --env CONSUME_DIR=/consume \
  --mount type=bind,source=/srv/paperless/consume,target=/consume \
  ghcr.io/zshel/brother-scan-to-paperless:brscan4
```

For `brscan5`, change the image tag and `SCANNER_MODEL`.

For API delivery, replace the consume-directory mount with persistent staging
and add the API settings:

```bash
docker volume create brother-scan-staging
docker run --detach \
  --name brother-scan-to-paperless \
  --restart unless-stopped \
  --network host \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --env PRINTER_IP=192.168.1.50 \
  --env HOST_IP=192.168.1.10 \
  --env SCANNER_MODEL=DCP-L2540DW \
  --env DELIVERY_MODE=api \
  --env PAPERLESS_URL=https://paperless.example.com \
  --env PAPERLESS_API_TOKEN=replace-with-a-paperless-token \
  --mount type=volume,source=brother-scan-staging,target=/staging \
  ghcr.io/zshel/brother-scan-to-paperless:brscan4
```

### Networking

The Compose file uses host networking, which is the most reliable option on
Linux because the printer must send scan-button events back to the advertised
`HOST_IP`.

Bridge networking can also work. Publish `54925/udp` and still set `HOST_IP`
to the Docker host's LAN IP:

```bash
docker run --publish 54925:54925/udp ...
```

Allow these flows through the host and network firewall:

| Direction | Protocol/port | Purpose |
|---|---|---|
| Printer to Docker host | UDP `54925` | scan-button event |
| Container to printer | UDP `161` | capability detection and legacy SNMP profile registration |
| Container to printer | TCP `443` | Phoenix HTTPS profile registration on newer printers |
| `brscan4`/`brscan5` to printer | TCP `54921`, `5566` | scan data requested by `scanimage` |
| Container to Paperless | configured HTTP(S) TCP port | document upload in API mode |

The containers do not require systemd, D-Bus, udev, or privileged access.
Button-profile registration automatically uses Phoenix HTTPS when the printer
advertises it and otherwise uses legacy SNMP. This registration and the UDP
button-event path are independent of the selected Brother backend.

## Kubernetes (Helm)

A Helm chart is published to GHCR with each release:

```bash
helm install brother-scan \
  oci://ghcr.io/zshel/charts/brother-scan-to-paperless \
  --version X.Y.Z \
  --namespace brother-scan --create-namespace \
  --values my-values.yaml
```

A minimal API-mode `my-values.yaml`:

```yaml
image:
  backend: brscan5        # or brscan4
scanner:
  printerIP: 192.168.1.111
  model: DCP-L2620DW
service:
  loadBalancerIP: 192.168.5.57   # also used as the scanner's event address
paperless:
  url: https://paperless.example.com
  existingSecret: brother-scan-paperless   # key: apiToken
  tags: [3, 14]                            # Paperless tag IDs, optional
```

The scanner sends Scan-button events to `scanner.hostIP` (default:
`service.loadBalancerIP`) on UDP port `54925`, so that address must be
reachable from the printer, typically through a MetalLB `LoadBalancer`
Service. The chart always runs a single replica with the `Recreate` strategy.
Supply the token through `paperless.existingSecret` (for example a
SealedSecret), or set `paperless.apiToken` to have the chart create the
Secret. Failed uploads are kept on the staging PersistentVolumeClaim
(`persistence.*`) for manual recovery.

Consume mode is also supported with `deliveryMode: consume` and
`consume.existingClaim` pointing at a claim shared with Paperless's consume
directory. See
[`charts/brother-scan-to-paperless/values.yaml`](charts/brother-scan-to-paperless/values.yaml)
for every option. Chart versions match application releases, and the chart's
default image tag is `<backend>-<appVersion>`.

## Native installation

The original systemd installation remains available for Debian-based hosts.

Install SANE first so Brother's package can link its backend into the correct
directory, then install the correct Brother driver for your model:

```bash
sudo apt-get update
sudo apt-get install ca-certificates libtiff-tools sane-utils snmp
```

After installing the Brother `.deb`, run:

```bash
git clone https://github.com/zshel/brother-scan-to-paperless.git
cd brother-scan-to-paperless
sudo bash install.sh
```

The installer adds the CA certificate bundle, `sane-utils`, `snmp`, and
`libtiff-tools`, installs the daemon under `/opt`, creates its staging directory,
and creates a systemd service. It expects either `brsaneconfig4` or
`brsaneconfig5` to already be installed.

Register the network scanner with the matching tool:

```bash
# brscan4
sudo brsaneconfig4 -a name=BROTHER model=DCP-L2540DW ip=192.168.1.50

# brscan5 example
sudo brsaneconfig5 -a name=BROTHER model=DCP-L3560CDW ip=192.168.1.50
```

Then configure, test, and start the daemon:

```bash
sudo brother-scan-to-paperless setup
sudo brother-scan-to-paperless test
sudo systemctl enable --now brother-scan-to-paperless
```

Consume mode needs writable access to Paperless's consume directory. API mode
instead needs a private local staging directory owned by the daemon user, with
no group or other access, plus HTTPS access to Paperless. The native setup
wizard atomically stores its configuration with mode `0600` because an API
configuration can contain a token.

## Usage

Use the printer's display to start a scan:

1. Press **Scan**.
2. Select **Scan to PC** and the configured display name, such as `Paperless`.
3. Select **File**.
4. Complete the document using the scanner's own workflow. Load all pages in
   the automatic document feeder before starting, or use the scanner's
   **Next Page** option when scanning successive pages from the flatbed. Finish
   the job on the scanner after the last page.
5. The daemon retrieves the complete job as one SANE batch and writes it to the
   consume directory or submits it to the API, depending on `DELIVERY_MODE`.

A one-page job is delivered normally. With `FORMAT=tiff` or `FORMAT=pdf`, all
pages in a multi-page job are assembled in scanner order and delivered as one
document. Select **File** only once; the scanner, rather than repeated button
events, controls when another page belongs to the current document.

## Configuration

Native configuration is read from
`/etc/brother-scan-to-paperless/config.json` or
`~/.config/brother-scan-to-paperless/config.json`. Environment variables
override JSON values, and supported command-line flags override both.

| JSON field | Environment variable | Default |
|---|---|---|
| `driver_version` | `BRSCAN_VERSION` | `4` natively; set by the image |
| `delivery_mode` | `DELIVERY_MODE` | `consume` |
| `printer_ip` | `PRINTER_IP` | required |
| `host_ip` | `HOST_IP` | auto-detected natively |
| `listen_port` | `LISTEN_PORT` | `54925` |
| `consume_dir` | `CONSUME_DIR` | required in consume mode; `/consume` in Compose |
| `staging_dir` | `STAGING_DIR` | required in API mode; `/staging` in Docker |
| `paperless_url` | `PAPERLESS_URL` | required in API mode |
| `paperless_token` | `PAPERLESS_API_TOKEN` | required in API mode |
| `paperless_api_timeout` | `PAPERLESS_API_TIMEOUT` | `60` seconds |
| `paperless_allow_insecure_http` | `PAPERLESS_ALLOW_INSECURE_HTTP` | `false` |
| `paperless_tags` | `PAPERLESS_TAGS` | none; tag IDs added to every API upload |
| `scanner_device` | `SCANNER_DEVICE` | derived from the driver version |
| `resolution` | `RESOLUTION` | `300` |
| `format` | `FORMAT` | `tiff` |
| `register_interval` | `REGISTER_INTERVAL` | `300` |
| `scan_timeout` | `SCAN_TIMEOUT` | `120` seconds for the complete scan job |
| `display_name` | `DISPLAY_NAME` | `Paperless` |
| `log_file` | `LOG_FILE` | host log path; empty in Docker |
| `source` | `SOURCE` | `FB`; `ADF` scans the feeder, `auto` scans the feeder if it has paper and otherwise the flatbed |
| `size` | `PAPER_SIZE` | reserved; currently not applied |

`PAPERLESS_API_TOKEN_FILE` is an environment-only alternative to
`PAPERLESS_API_TOKEN`; the file must contain only the token.

Docker also uses these scanner-registration variables:

| Variable | Description | Default |
|---|---|---|
| `SCANNER_MODEL` | exact model used by `brsaneconfig4/5` | required |
| `SCANNER_NAME` | internal Brother registration name | `BROTHER` |
| `AUTO_REGISTER_SCANNER` | replace and verify a proprietary Brother registration on `run` and `test` | `true` |
| `SKIP_SCANNER_REGISTRATION` | disable proprietary Brother registration when set to `true` | `false` |

## Troubleshooting

### The printer shows "Connecting" and times out

- Confirm the container logs contain `Listening on UDP port 54925`.
- Confirm `HOST_IP` is the Docker host's LAN IP and is reachable from the
  printer.
- Check the firewall allows printer-to-host UDP `54925`.
- Do not start the `brscan4` and `brscan5` Compose profiles together.
- Stop Brother's `brscan-skey` process or service; it also registers scan
  profiles and listens on UDP `54925`, so it must not run beside this daemon.
  The daemon refuses to start when another process already owns that port.

### The scanner is not found

Check the SANE registration and device list inside the selected service:

```bash
docker compose --profile brscan4 exec brscan4 brsaneconfig4 -q
docker compose --profile brscan4 exec brscan4 scanimage -L
```

For `brscan5`, replace the profile, service, and command suffix with `5`.
An invalid `SCANNER_MODEL` or the wrong driver generation produces an
unverified-registration warning; the daemon continues, but scanning may not be
available through that Brother device.

### Button-profile registration fails

The one-time net-snmp message
`Created directory: /var/lib/snmp/cert_indexes` is harmless. It is not a
registration failure.

An SNMP `noSuchName` or `noAccess` error for the Brother registration OID on a
newer printer usually means the running application predates Phoenix HTTPS
registration and is still trying the legacy SNMP write. Update the image or
application. The current daemon detects Phoenix-capable models automatically;
allow outbound UDP `161` for detection and TCP `443` for registration.

### Consume-directory scans are not imported

- Confirm `PAPERLESS_CONSUME_DIR` points to the actual host directory shared
  with Paperless.
- Confirm the directory exists and is writable by the container.
- On SELinux systems, recreate the scanner container after changing the mount
  so Docker applies its shared `container_file_t` label:

  ```bash
  docker compose --profile brscan4 up --detach --force-recreate
  ls -Zd /srv/paperless/consume
  ```

- Check the Paperless task worker logs for import or permission errors.

### Only the first page is imported

Select **File** once, then complete the multi-page job on the scanner. Load the
entire document in the automatic document feeder before starting, or use the
scanner's **Next Page** option for each additional flatbed page and finish the
job only after the last page. Keep `FORMAT` set to `tiff` or `pdf` so the batch
can be delivered as one document.

The logs report how many pages the SANE batch returned. If the batch contains
only one page, confirm that the correct Brother backend and model are
registered, and that the scanner is using its document-feeder or continuation
workflow rather than ending the job after the first page. Increase
`SCAN_TIMEOUT` if a long feeder or **Next Page** job needs more than 120 seconds;
the timeout covers the complete batch.

### API uploads fail

- Confirm `PAPERLESS_URL` is reachable from the container and does not include
  `/api` at the end.
- HTTP `401` indicates an invalid token. HTTP `403` usually means the token user
  lacks permission to add documents.
- For HTTPS, use a certificate trusted by the container's CA store. TLS
  verification is never disabled by this daemon.
- Plain HTTP is rejected unless `PAPERLESS_ALLOW_INSECURE_HTTP=true`; use that
  opt-in only on a trusted, isolated development network.
- Check reverse-proxy request-size and timeout limits if larger scans fail.
- A failed or uncertain upload remains in `/staging`. Do not blindly resubmit
  after a timeout because Paperless may already have queued it.
- Never submit `.partial` files. Files ending in `.accepted` were already
  accepted and are retained only because local cleanup failed.
- A successful request logs a task identifier. Check its asynchronous result at
  `/api/tasks/?task_id=<uuid>` or in the Paperless task UI.

Paperless documents token creation, upload permissions, and task semantics in
its [REST API guide](https://docs.paperless-ngx.com/api/#posting-documents).

### Test a scan from the container

Place a page on the scanner, then run:

```bash
docker compose --profile brscan4 exec brscan4 \
  brother-scan-to-paperless test
```

For `brscan5`, use the corresponding profile and service name.

## How it works

```text
Brother printer
    |  1. Profile registration (container -> printer UDP 161 or TCP 443)
    |  2. Button event (printer -> host UDP 54925)
    |  3. Scan pull (container -> printer through the selected Brother backend)
    v
brother-scan-to-paperless
    |  consume mode: writes TIFF/PDF/PNG/PNM to the shared directory
    |  api mode: stages locally, then POSTs the document
    v
Paperless-ngx
```

On startup and at the configured interval, the daemon queries the printer's
capabilities and registers scan profiles through Phoenix HTTPS or legacy SNMP.
A button event containing `BUTTON=SCAN` starts one `scanimage` batch. For TIFF
and PDF jobs, the SANE backend returns each page in the scanner's current job as
a numbered TIFF, then signals when the document is complete. The daemon keeps
those pages in scanner order, uses `tiffcp` to assemble a multi-page TIFF, and
runs `tiff2pdf` when PDF output is configured. A one-page batch follows the same
path without a merge. Single-page PNG and PNM jobs retain their configured
format directly.

Pages and assembled documents use partial paths until capture and conversion
finish, then the daemon atomically gives the completed document its final name.
Consume mode leaves that file in Paperless's consume directory. API mode sends
it to Paperless's documented upload endpoint and deletes the staged copy only
after a valid HTTP 200 JSON acknowledgement.

## Development

The implementation lives in the `src/brother_scan_to_paperless` package.
`src/brother_scan_daemon.py` is the thin executable entry point used by native
and container installations.

Run the unit tests with branch coverage:

```bash
python -m pip install --requirement requirements-dev.txt
PYTHONPATH=src python -m coverage run -m unittest discover -s tests -t . -v
python -m coverage report
```

CI requires at least 80% coverage of the application package.

### Releases

Releases are cut manually: run the **Release** workflow from the Actions tab
on `main` and choose `patch`, `minor` or `major`. The workflow bumps the
application version and the Helm chart `version` and `appVersion` together,
commits and tags `vX.Y.Z` on `main`, creates a GitHub release with notes
generated from the merged pull requests and commits since the previous
release, and then publishes the container images and Helm chart for that tag.

## Tested on

- Brother DCP-L2540DW with `brscan4`
- Proxmox LXC on Debian 12/13
- Docker build and smoke tests for both targets on Debian Bookworm
  (`linux/amd64`)
- Paperless-ngx native and bind-mounted consume directories
- Paperless API transport against an automated local HTTP endpoint

## License

The daemon and repository files are MIT licensed; see [LICENSE](LICENSE).
The `brscan4` and `brscan5` images contain Brother's proprietary scanner driver
under the
[Brother Linux driver EULA](https://support.brother.com/g/s/agreement/English_lpr/agree.html).
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before building or
redistributing an image. The proprietary images published on GHCR redistribute
the same unmodified Brother packages and remain subject to Brother's
agreement.
