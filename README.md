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

- registers scan profiles on the printer over SNMP;
- listens for button events on UDP port `54925`;
- uses `scanimage` to pull the document from the scanner; and
- either writes the result into Paperless-ngx's consume directory or uploads
  it through the Paperless API.

## Choose a driver

Brother models use either `brscan4` or `brscan5`. `brscan5` is not an upgrade
for `brscan4` models. Find your model on
[Brother Support](https://support.brother.com/) and use the scanner driver
generation listed on its Linux download page.

This repository builds two image targets:

| Published image | Brother backend | SANE device |
|---|---|---|
| `ghcr.io/zshel/brother-scan-to-paperless:brscan4` | `brscan4` 0.4.11-1 | `brother4:net1;dev0` |
| `ghcr.io/zshel/brother-scan-to-paperless:brscan5` | `brscan5` 1.7.0-0 | `brother5:net1;dev0` |

Brother currently publishes these Debian packages for x86 only. The Docker
targets intentionally support `linux/amd64` and fail early on other
architectures.

## Docker

Docker is the recommended installation method. The images include Python,
SANE, SNMP tools, this daemon, and the selected Brother driver. They are for
static-IP network scanners; USB scanners are not supported by this container
setup.

### Requirements

- Linux with Docker Engine and Docker Compose
- an `amd64` host, or an environment able to run `linux/amd64` images
- a Brother network scanner with a static or reserved IP address
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
driver-specific `brscanN-sha-<commit>` tag. There is no unqualified `latest`
tag because the two Brother backends are not interchangeable. For a
reproducible deployment, set `BRSCAN4_IMAGE` or `BRSCAN5_IMAGE` in `.env` to a
full versioned tag or image digest.

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
   because API mode can store a Paperless token there. The consume directory
   must already exist; Compose deliberately refuses to create a mistyped path.

3. Pull and start exactly one driver profile:

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

The entrypoint removes and re-adds the scanner's SANE network registration on
every start, then launches the daemon in the foreground. In consume mode,
scans are written to the bind-mounted `/consume` directory. Startup fails if
`PAPERLESS_CONSUME_DIR` is empty, which prevents scans from being left in an
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
Start the selected driver profile with the same Compose commands shown above.
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

For deployments that inject secrets from files, set
`PAPERLESS_API_TOKEN_FILE` to a readable file inside the container instead of
setting `PAPERLESS_API_TOKEN`. A token file takes precedence when both are
configured. Mount that file separately into the container; a host path is not
made available merely by putting it in `.env`.

### Build both images locally

```bash
docker build --platform linux/amd64 --target brscan4 \
  --tag brother-scan-to-paperless:brscan4 .

docker build --platform linux/amd64 --target brscan5 \
  --tag brother-scan-to-paperless:brscan5 .
```

The Dockerfile downloads the pinned official Brother packages and verifies
their SHA-256 checksums before installing them.

### Run without Compose

This example uses the `brscan4` image:

```bash
docker run --detach \
  --name brother-scan-to-paperless \
  --restart unless-stopped \
  --network host \
  --cap-drop ALL \
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
| Container to printer | UDP `161` | SNMP profile registration |
| Container to printer | TCP `54921`, `5566` | scan data requested by `scanimage` |
| Container to Paperless | configured HTTP(S) TCP port | document upload in API mode |

The static-IP workflow does not require systemd, D-Bus, an Avahi daemon, udev,
or privileged container access.

## Native installation

The original systemd installation remains available for Debian-based hosts.
Install SANE first so Brother's package can link its backend into the correct
directory, then install the correct Brother driver for your model:

```bash
sudo apt-get update
sudo apt-get install ca-certificates sane-utils snmp
```

After installing the Brother `.deb`, run:

```bash
git clone https://github.com/zshel/brother-scan-to-paperless.git
cd brother-scan-to-paperless
sudo bash install.sh
```

The installer adds the CA certificate bundle, `sane-utils`, and `snmp`, installs
the daemon under `/opt`, creates its staging directory, and creates a systemd
service. It expects either `brsaneconfig4` or `brsaneconfig5` to already be
installed.

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

Once the daemon is running, use the printer's display:

1. Press **Scan**.
2. Select **Scan to PC** and the configured display name, such as `Paperless`.
3. Select **File**. Other registered scan buttons also trigger the same scan.
4. The daemon writes the document to the consume directory or submits it to
   the API, depending on `DELIVERY_MODE`.

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
| `scanner_device` | `SCANNER_DEVICE` | derived from driver version |
| `resolution` | `RESOLUTION` | `300` |
| `format` | `FORMAT` | `tiff` |
| `register_interval` | `REGISTER_INTERVAL` | `300` |
| `scan_timeout` | `SCAN_TIMEOUT` | `120` |
| `display_name` | `DISPLAY_NAME` | `Paperless` |
| `log_file` | `LOG_FILE` | host log path; empty in Docker |
| `source` | `SOURCE` | reserved; currently not applied |
| `size` | `PAPER_SIZE` | reserved; currently not applied |

`PAPERLESS_API_TOKEN_FILE` is an environment-only alternative to
`PAPERLESS_API_TOKEN`; the file must contain only the token.

Docker also requires these entrypoint variables:

| Variable | Description | Default |
|---|---|---|
| `SCANNER_MODEL` | exact Brother model used by `brsaneconfig4/5` | required |
| `SCANNER_NAME` | internal SANE registration name | `BROTHER` |

## Troubleshooting

### The printer shows "Connecting" and times out

- Confirm the container logs contain `Listening on UDP port 54925`.
- Confirm `HOST_IP` is the Docker host's LAN IP and is reachable from the
  printer.
- Check the firewall allows printer-to-host UDP `54925`.
- Do not start the `brscan4` and `brscan5` Compose profiles together.

### The scanner is not found

Check the SANE registration and device list inside the selected service:

```bash
docker compose --profile brscan4 exec brscan4 brsaneconfig4 -q
docker compose --profile brscan4 exec brscan4 scanimage -L
```

For `brscan5`, replace the profile, service, and command suffix with `5`.
An invalid `SCANNER_MODEL` or the wrong driver generation prevents startup.

### Consume-directory scans are not imported

- Confirm `PAPERLESS_CONSUME_DIR` points to the actual host directory shared
  with Paperless.
- Confirm the directory exists and is writable by the container.
- Check the Paperless task worker logs for import or permission errors.

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

## How it works

```text
Brother printer
    |  1. SNMP registration (container -> printer UDP 161)
    |  2. Button event (printer -> host UDP 54925)
    |  3. Scan pull (container -> printer TCP 54921/5566)
    v
brother-scan-to-paperless
    |  consume mode: writes TIFF/PNG/PNM to the shared directory
    |  api mode: stages locally, then POSTs the document
    v
Paperless-ngx
```

On startup and at the configured interval, the daemon registers scan profiles
with the printer. A button event containing `BUTTON=SCAN` triggers
`scanimage`. The daemon scans to a `.partial` path and atomically gives the
completed document its final name. Consume mode leaves that file in
Paperless's consume directory. API mode streams it to Paperless's documented
upload endpoint, deleting the staged copy only after a valid HTTP 200 JSON
acknowledgement.

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

## Tested on

- Brother DCP-L2540DW with `brscan4`
- Proxmox LXC on Debian 12/13
- both Docker targets on Debian Bookworm (`linux/amd64`)
- Paperless-ngx native and bind-mounted consume directories
- Paperless API transport against an automated local HTTP endpoint

## License

The daemon and repository files are MIT licensed; see [LICENSE](LICENSE).
The Docker images also contain Brother's proprietary scanner driver under the
[Brother Linux driver EULA](https://support.brother.com/g/s/agreement/English_lpr/agree.html).
See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) before building or
redistributing an image. The images published on GHCR redistribute the same
unmodified packages and remain subject to Brother's agreement.
