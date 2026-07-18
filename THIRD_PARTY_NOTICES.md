# Third-party notices

The Docker images combine this repository's MIT-licensed code with a Brother
scanner driver and Debian runtime packages. Those third-party components retain
their own licenses.

## Brother driver images

The `brscan4` and `brscan5` targets include proprietary scanner driver software
from Brother Industries, Ltd. The Brother driver is not covered by this
repository's MIT license.

Brother makes its Linux driver available under the
[Brother End-User License Agreement](https://support.brother.com/g/s/agreement/English_lpr/agree.html),
which includes terms for reproduction and redistribution. Downloading or using
the driver is subject to that agreement.

The images published as
`ghcr.io/zshel/brother-scan-to-paperless:brscan4` and
`ghcr.io/zshel/brother-scan-to-paperless:brscan5` redistribute the unmodified
Brother packages listed below. The repository's MIT license applies only to
this project's code and files; it does not relicense Brother software.

Brother's agreement grants a non-exclusive license to reproduce and distribute
the software over the Internet or by other means. Downloading or using a
published image includes use of Brother software and is subject to that
agreement, including its warranty disclaimer.

The image builds use these unmodified official packages:

| Package | Official artifact | SHA-256 |
|---|---|---|
| `brscan4` 0.4.11-1 | [amd64 Debian package](https://download.brother.com/welcome/dlf105200/brscan4-0.4.11-1.amd64.deb) | `027b73648722ac8c8eb1a9c419d284a6562cc763feac9740a2b75a683b092972` |
| `brscan5` 1.7.0-0 | [amd64 Debian package](https://download.brother.com/welcome/dlf104033/brscan5-1.7.0-0.amd64.deb) | `9c3a200db3dc7b2b3332dff8c5226b4f3ee560d20b2e1f4918b1faf7124517b2` |

This notice highlights the Brother scanner drivers. Debian runtime package
notices remain under `/usr/share/doc` in every image, and the Python base-image
license remains under `/usr/local/lib/python3.12/LICENSE.txt`.

Brother's packages state: "This software is based in part on the work of the
Independent JPEG Group."
