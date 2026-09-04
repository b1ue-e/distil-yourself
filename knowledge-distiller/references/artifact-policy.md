# Domain Draft Artifact Policy

The domain draft is a non-executable bundle with a closed allowlist.

## Allowed

- One regular UTF-8 `SKILL.md` file.
- One-level regular UTF-8 files under `references/` with `.md`, `.txt`, `.json`, `.yaml`, or `.yml` extensions.
- Regular `.png`, `.jpg`, `.jpeg`, or `.webp` assets whose magic bytes match the extension and whose digest belongs to the compiler's reviewed template allowlist.

The Foundation CLI has no reviewed template assets and therefore rejects all assets. Its library accepts a compiler-owned digest set so later compiler code can inject a shipped allowlist without taking trust from CLI input.

## Rejected

- Any unknown root entry or nested reference directory.
- Executable bits, scripts, macros, package manifests, archives, HTML, SVG, PDF, or Office files.
- Symlinks, hardlinks, devices, FIFOs, sockets, path traversal, or case-fold collisions.
- Invalid UTF-8 or extension/content mismatches.
- Instructions that ask an agent to create, synthesize, or run code, commands, scripts, or packages.

Unexpected extended attributes are rejected. The validator ignores only the macOS-managed `com.apple.provenance` attribute, which this runtime automatically adds to newly created filesystem objects; it does not treat that attribute as domain content or authority.

Validation is bounded to 128 entries, 8 MiB per file, and 24 MiB total. Files and directories are opened relative to pinned directory descriptors and their identities are rechecked to reject substitution races. JSON references must parse as strict JSON; `.yaml` and `.yml` are limited to the JSON-compatible YAML subset in this dependency-free milestone.

Validation produces a sorted manifest containing normalized path, SHA-256 digest, byte size, and media type. A successful manifest does not authorize export or installation.
