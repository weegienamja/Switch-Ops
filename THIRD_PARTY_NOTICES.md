# Third-party provenance

SwitchOps uses established open-source components obtained from their standard
package registries. Dependency versions are pinned or resolved in
`backend/requirements.txt`, `pnpm-lock.yaml`, and
`desktop/src-tauri/Cargo.lock`; those files are authoritative for a build.

The v0.4.1 release review checked installed package metadata and lockfile source
records for the principal runtime and build dependencies:

| Ecosystem | Principal components | Upstream license family |
| --- | --- | --- |
| Python / PyPI | FastAPI, Uvicorn, Pydantic, Netmiko, keyring | MIT / BSD |
| Python / PyPI | Paramiko | LGPL-2.1 |
| Python build | PyInstaller | GPL-2.0-or-later with the PyInstaller bootloader exception |
| JavaScript / npm | React, Next.js, Motion | MIT |
| Rust / crates.io | Tauri, reqwest, Tokio, Serde | MIT and/or Apache-2.0 |

Transitive packages retain their own copyright and license terms. This file is
not a replacement for those terms; registry metadata and the license files
shipped by each dependency remain controlling. The Windows WebView2 runtime is
provided under Microsoft's terms and is not vendored in this source tree.

No third-party source archive, credential, private package, or development
machine path is committed to this repository. Release binaries are produced
from the committed manifests and lockfiles and are distributed as GitHub
Release assets rather than source-history contents.
