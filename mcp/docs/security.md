# Security

This server runs inside the trust boundary of an MCP client, which changes what
"secure" means. A stdio MCP server has **no sandbox**: it runs as the same user
as the client, with the same filesystem access. The client cannot contain it.

So the threat model is not "stop the server doing something it was not asked
to", it is "do not be a liability in a catalog the model reads".

## The risk specific to MCP server authors: tool poisoning

Tool **names, titles, and descriptions are instructions the model reads**. They
are injected into the model's context before any tool is called, so a malicious
or careless description can steer an agent — including how it uses *other*
servers' tools — without the poisoned tool ever running. The official guidance
calls this a poisoned tool catalog, and notes that "rug pulls" (changing a tool
definition after a user approved it) defeat one-time consent.

The rules this repository follows:

- **Descriptions describe; they never instruct.** `flowscope_get_report` says
  what it returns. It does not say "always call this first" or "do not tell the
  user what you are doing". A description that tries to direct the model is
  indistinguishable from an attack.
- **No invisible characters and no comments** anywhere in a name, title, or
  description. Zero-width and bidirectional control characters
  (U+200B–200F, U+202A–202E, U+2060, U+FEFF) and `<!-- -->` blocks are the
  standard smuggling channels. The strings here are plain ASCII prose.
- **No credential paths.** Nothing references `~/.ssh`, `.env`, `.aws`, or
  `/etc/passwd`, directly or allusively.
- **Anything that transmits data says so.** `flowscope_analyze_video` and
  `flowscope_compare_videos` carry `openWorldHint: true` because they cause
  network fetches and paid calls, and their descriptions state the cost.
- **Annotations are honest.** See the table in
  [server-internals.md](server-internals.md#annotations-are-not-optional). A lie
  in an annotation is a supply-chain attack on every user of the client, not a
  cosmetic bug.
- **Definitions are treated as a governed artifact.** Changing a tool's
  signature, description, or annotations is a change to what every installed
  client's model reads, so it goes through review and a version bump rather than
  a drive-by edit.

## Data flow and what leaves the machine

Worth being explicit, because "it analyses a video" hides several transfers:

| Destination | What is sent | When |
| --- | --- | --- |
| The FlowScope backend | Video URLs, video ids, comparison id lists | On every tool call |
| YouTube | Nothing; the backend downloads the video | During analysis |
| The configured LLM provider | Extracted frames (images) and transcript excerpts | During analysis |

The last row is the one users most often do not expect: **frames of the video
and transcript text are sent to whichever LLM provider the backend is configured
with**. That is a decision made in `backend/.env`, not here, but it is worth
stating in a client-facing description rather than leaving implied.

Downloaded videos, frames, transcripts, and the database stay on the backend
host.

## No tokens are handled here

This server holds exactly one optional credential, `FLOWSCOPE_API_TOKEN`, for a
backend behind an authenticating proxy. Two rules from the MCP authorisation
spec that it respects by construction:

- It **never accepts a token it was not issued**. There is no passthrough; the
  client's own credentials are never forwarded to the backend.
- It **never logs the token**. The value is only ever placed in an
  `Authorization` header.

Because the server talks to `localhost` over plain HTTP, the token is not
protected in transit. That is acceptable for a loopback service and not
acceptable for a remote one; if the backend is ever exposed, put TLS in front of
it.

## Input handling

- **Tool arguments are validated by the SDK** against the JSON Schema generated
  from the type hints, before the handler runs. There is no re-validation here,
  and no `eval`, no shell invocation, and no string interpolation into a command
  anywhere in the package.
- **Video ids and frame ids are opaque.** They are passed to the backend as URL
  path segments rather than trusted as locators. The backend is the authority on
  whether they exist.
- **Frame images are size-capped.** `fetch_image` refuses anything over 12 MiB
  rather than reading an unbounded response into memory.
- **Resource templates do not touch the filesystem.** The SDK rejects `..`,
  absolute paths, and null bytes in template parameters by default, and these
  handlers never open a file — they only call the backend.

## Deployment notes

- **Bind to loopback.** The stdio transport has no network surface. The HTTP
  transports default to `127.0.0.1`; do not change that to `0.0.0.0` without
  putting authentication in front of it. Any local process can otherwise spend
  the user's LLM credits.
- **Rate-limit a shared instance.** The backend is single-user by design. A
  server exposed to several clients inherits no per-caller limits, so a runaway
  agent could run many analyses at once. `MAX_CONCURRENT_JOBS` on the backend is
  the backstop.
- **Prefer stdio for local use.** It avoids the network surface entirely, and it
  means the client and server share one trust domain deliberately rather than by
  accident.
- **Log to stderr.** stdio's stdout is the protocol wire; a diagnostic written
  there corrupts the session. MCP-level logging is deprecated in the current
  revision, so stderr is the correct destination.

## Supply chain

Because a stdio server is not sandboxed, its dependency tree is part of its
attack surface:

- Dependencies are deliberately minimal — `mcp`, `httpx2`, and `pydantic`.
  Nothing from the backend's heavy stack is reachable from this package, and a
  test asserts that importing it does not pull in `torch`, `faster_whisper`,
  `ctranslate2`, or `yt_dlp`.
- `mcp>=2.3,<3` is bounded so a future major cannot change the wire behaviour
  silently.
- The published artifacts are a wheel and an sdist; the wheel bundles the two
  skills and the plugin manifest as data, which is inert.

## Reporting

Security issues should go to the repository's issue tracker, or privately to the
maintainer for anything exploitable.
