# Releasing FlowScope

Releases are automated. You bump a version, tag it, push the tag, and
[`.github/workflows/release.yml`](../../.github/workflows/release.yml) verifies
everything, publishes to PyPI, publishes to the MCP Registry, and creates a
GitHub release with the built artifacts attached.

Publishing is **never** a side effect of a merge: the release workflow runs on
version tags and on manual dispatch, and on nothing else.

## The normal path

```bash
cd mcp
./.venv/bin/python scripts/version.py bump 0.2.0     # rewrites all five files
cd ..

git add -A
git commit -m "release: v0.2.0"
git tag -a v0.2.0 -m "v0.2.0"
git push && git push --tags
```

That is the whole release. Watch it under **Actions → Release MCP server**.

### Why a script bumps the version

Six version declarations live in five files, and the MCP Registry requires the
server version and the version inside its `packages[]` entry to match:

| File | What it versions |
| --- | --- |
| `mcp/pyproject.toml` | The distribution — **the single source of truth** |
| `mcp/src/flowscope_mcp/__init__.py` | `__version__`, reported by `flowscope-mcp --version` |
| `mcp/server.json` | The registry record, **twice** (server and package) |
| `mcp/.claude-plugin/plugin.json` | The plugin manifest |
| `.claude-plugin/marketplace.json` | The marketplace manifest |

Editing them by hand is how a release fails *during* publishing — and by then it
is expensive, because **PyPI permanently refuses to reuse a version number**. A
half-published release cannot be retried with the same version; it needs a new
one. So the checks run before anything is uploaded:

```bash
python scripts/version.py show         # what every file currently says
python scripts/version.py check        # fail if any disagree, or if the tag does
python scripts/version.py prerelease   # 'true' for 0.2.0-rc1, 'false' for 0.2.0
```

`check` also compares against the git tag when `HEAD` is tagged, which catches
the specific mistake of tagging `v0.2.0` while the files still say `0.1.0`.

## The manual path

Everything the tag path does is also available on demand, without a tag:
**Actions → Release MCP server → Run workflow**.

| Input | Purpose |
| --- | --- |
| `version` | Optional. Bumps the working tree before building, so you can release without editing files first. Nothing is committed for you. |
| `ref` | Optional. The branch, tag, or SHA to release from. |
| `publish` | **Defaults to false.** Off means every check runs and the artifacts are built and uploaded as a workflow artifact, but nothing leaves the machine. |

Turn `publish` on to push to PyPI. Prerelease classification still comes from the
version string, and a `-`-suffixed version is marked as a GitHub prerelease.

A dry run is the useful first step for a new release process:

```
version: 0.2.0, publish: false    # verifies and builds, publishes nothing
```

Manual runs typically come from a branch, so **no tag exists**. In that case the
workflow creates the tag at the released commit as part of making the GitHub
release, which keeps every published version anchored to a real revision.

## What the workflow does, in order

All of this happens in `prepare`, and **any failure stops the release before an
upload**:

1. Resolve the version and the tag, and classify a prerelease.
2. `scripts/version.py check` — every file agrees, and the tag matches.
3. **Refuse to continue if that version already exists on PyPI.** PyPI allows no
   reuse, so this turns an unrecoverable upload error into a readable message.
4. Run the shared verification action — the same one CI uses, so a tag cannot
   publish something that would have failed CI:
   - `ruff check`
   - the full `pytest` suite
   - `skills-ref validate` on both skills
   - `server.json` against the official registry schema, plus the README
     ownership marker
   - `claude plugin validate --strict` on both manifests
   - `python -m build`, then assert the wheel actually contains the skills and
     plugin manifests
5. Upload the wheel and sdist as a workflow artifact.

Then three jobs follow, deliberately separated:

| Job | What it does | Why separate |
| --- | --- | --- |
| `publish-pypi` | `uv publish` with **trusted publishing** | No PyPI token is stored in the repository. Isolated so a later failure cannot undo an upload. |
| `publish-registry` | `mcp-publisher login github-oidc` then `publish` | Must run *after* PyPI, because the registry proves package ownership by reading the published README. Waits up to 5 minutes for PyPI to serve the new version. |
| `github-release` | Creates the release and attaches the wheel and sdist | Gives the version a stable download URL. |

The registry job is `continue-on-error`: the MCP Registry is still in preview,
and its availability should not fail a release or force a re-tag when PyPI has
already succeeded.

## One-time setup before the first release

Two repository settings, neither of which is a secret:

1. **PyPI trusted publishing.** On PyPI, add a *pending* publisher for
   `flowscope-mcp` with:
   - Owner: `DavidNgugi` · Repository: `flowscope`
   - Workflow: `release.yml` · Environment: `release`

2. **The `release` GitHub environment.** Create it under Settings →
   Environments. Matching the PyPI publisher's environment name is what scopes
   the OIDC token. Add required reviewers there if you want a human to approve
   each publish — recommended, and it costs one click.

No `PYPI_API_TOKEN` and no DNS key are needed: PyPI uses OIDC, and
`server.json` claims the `io.github.DavidNgugi/*` namespace, which
`mcp-publisher login github-oidc` proves from inside the workflow.

## Version numbering

Plain `X.Y.Z`, optionally with a hyphenated prerelease: `0.2.0`, `0.2.0-rc1`,
`1.0.0-alpha.2`. Build metadata (`2.0.0+build.5`) is allowed and is **not**
treated as a prerelease.

Rejected, because the MCP Registry rejects ranges and the tooling refuses to
write something the registry will refuse:

| Rejected | Use instead |
| --- | --- |
| `^0.2.0`, `~0.2.0`, `>=0.2.0` | `0.2.0` |
| `0.2.*`, `0.2.x` | `0.2.0` |
| `latest` | a real version |
| `1.0.0a1` (separatorless) | `1.0.0-a1` |

Published versions are immutable and old ones stay resolvable, so a mistake is
corrected by publishing the next version, not by editing the old record.

## If something goes wrong

**The workflow failed before uploading.** Nothing is published. Fix it and push
the tag again:

```bash
git push --delete origin v0.2.0 && git tag -d v0.2.0
git tag -a v0.2.0 -m "v0.2.0" && git push --tags
```

**PyPI published but the registry step failed.** Harmless: the registry job is
non-fatal and retryable. Re-run just that job from the Actions UI.

**The version is wrong and PyPI already has it.** PyPI will not let you reuse
it. Bump to the next version and release that; if the bad release is genuinely
harmful, yank it on PyPI and deprecate the registry entry:

```bash
mcp-publisher status --status deprecated --message "superseded by 0.2.1" \
  io.github.DavidNgugi/flowscope 0.2.0
```

## Other distribution channels

Publishing to PyPI and the registry covers `uvx` installs and discovery. Two
further channels are intentional additions rather than part of the automated
release:

**Plugin marketplace.** Users install the skills and the server together
straight from the repository — no publishing step is involved:

```bash
/plugin marketplace add DavidNgugi/flowscope
/plugin install flowscope@flowscope
```

Or, from a local checkout: `claude --plugin-dir /abs/path/to/flowscope/mcp`.

**Skills alone**, for clients with no marketplace:

```bash
cp -r mcp/skills/* .agents/skills/     # project scope, cross-client convention
npx skills add DavidNgugi/flowscope    # the `skills` CLI, ~80 agent targets
```

## What is deliberately not shipped

- **No `smithery.yaml`.** Smithery's current flow is URL- or MCPB-based, and its
  old config-file documentation has been withdrawn. Treat that file as legacy.
- **No `/.well-known/mcp` discovery file.** There is no such standard. The
  similar-looking paths come from a Smithery-specific convention and an expired
  IETF draft; the only normative `.well-known` paths in MCP are the OAuth ones,
  which apply to remote servers requiring authorisation, not to this one.
- **No `.mcpb` bundle.** It would add a pack-sign-hash pipeline for a channel
  consumed only by Claude Desktop's one-click install and Smithery. `pypi` plus
  `uvx` reaches more clients with less machinery. Adding one later is
  self-contained: `mcpb pack`, then a release asset whose URL contains "mcp"
  plus a `fileSha256` in `server.json`.
