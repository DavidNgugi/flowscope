# Installing the skills

The two FlowScope skills are plain [Agent Skills](https://agentskills.io)
directories, so they install into any compatible client. This page covers every
route: the one-command installer, the plugin, and a verified directory matrix for
copying them by hand.

The skills and the MCP server are **independent**. The skills are useful without
the server (they teach an agent to read a report it already has), and the server
works without them. Install both for the intended experience.

Once installed, [usage.md](usage.md) shows what to type.

| Skill | Use it when |
| --- | --- |
| `analyzing-product-demo-ux` | A teardown of one recorded flow — how onboarding, signup, or checkout works, screen by screen. |
| `comparing-product-demo-ux` | Several products compared: shared conventions, real divergences, what is worth borrowing. |

## Which route to take

| Route | Best for | Command |
| --- | --- | --- |
| Plugin | Claude Code, and you want the skills *and* the server | `/plugin marketplace add DavidNgugi/flowscope` |
| `.agents/skills/` | Almost every other client, one directory | `mkdir -p .agents/skills && cp -r …` |
| Personal skills dir | You want them in every project, one client | `cp -r … ~/.claude/skills/` (etc.) |
| `skills` CLI | Distributing to teammates or ~80 agent targets | `npx skills add DavidNgugi/flowscope` |

## Route 1: the plugin (skills and server together)

Recommended for Claude Code: one install brings the skills, the prompts, and the
MCP server registration.

```bash
/plugin marketplace add DavidNgugi/flowscope
/plugin install flowscope@flowscope
```

From a local checkout, with no publishing involved:

```bash
claude --plugin-dir /absolute/path/to/flowscope/mcp
```

The plugin's `.mcp.json` launches the server with `uvx`, resolving the package
from the plugin directory, so **no separate `flowscope-mcp-install` step is
needed** when you install this way.

## Route 2: `.agents/skills/` — the portable choice

The Agent Skills specification deliberately does **not** mandate where skill
directories live; it only defines what is inside them. In practice
`.agents/skills/` has become the cross-client convention, and for most clients it
is the *project* path. Installing there means several clients see the same
skills, and a colleague who clones the repo gets them too.

```bash
# From the repository root
mkdir -p .agents/skills
cp -r mcp/skills/* .agents/skills/

# Or per user, for every project on the machine
mkdir -p ~/.agents/skills
cp -r mcp/skills/* ~/.agents/skills/
```

Verify the layout — each skill is a directory containing `SKILL.md`:

```
.agents/skills/
├── analyzing-product-demo-ux/
│   └── SKILL.md
└── comparing-product-demo-ux/
    └── SKILL.md
```

## Route 3: a client's own directory

Use this when a client does not scan `.agents/skills/`, or when you want the
skills only in that client. These paths are verified against each project's
documentation; the project paths are relative to the repository root.

| Client | Project | Personal |
| --- | --- | --- |
| Claude Code | `.claude/skills/` | `~/.claude/skills/` |
| Codex CLI | `.agents/skills/` | `~/.codex/skills/` |
| Cursor | `.agents/skills/`, `.cursor/skills/` | `~/.cursor/skills/` |
| VS Code / Copilot | `.github/skills/`, `.claude/skills/`, `.agents/skills/` | `~/.copilot/skills/` |
| Gemini CLI | `.gemini/skills/` or `.agents/skills/` | `~/.gemini/skills/` |
| Zed, Cline | `.agents/skills/` | `~/.agents/skills/` |
| OpenCode | `.agents/skills/` | `~/.config/opencode/skills/` |
| Factory Droid | `.agents/skills/` | `~/.factory/skills/` |

Notes that save debugging time:

- **Claude Code** loads project skills from `.claude/skills/` in the start
  directory *and every parent up to the repository root*, so a nested install
  still works. `~/.claude/skills/synced/` is reserved for account-synced skills —
  do not write there by hand.
- **Cursor** additionally reads `.claude/skills/` and `.codex/skills/` for
  compatibility, so an existing Claude Code install is picked up for free.
- **Gemini CLI** resolves `.agents/skills/` ahead of `.gemini/skills/` at the
  same tier, so the portable path wins if both exist.
- **Codex** scans `.agents/skills/` in every directory from the current working
  directory up to the repository root.

```bash
# Example: personal scope for Claude Code
mkdir -p ~/.claude/skills
cp -r mcp/skills/* ~/.claude/skills/
```

## Route 4: the `skills` CLI

The `skills` CLI (Vercel Labs) distributes the same directories to roughly eighty
agent targets and is the right tool for a team:

```bash
npx skills add DavidNgugi/flowscope            # into the current project
npx skills add DavidNgugi/flowscope -g         # globally, for the user
npx skills list                                # what is installed
```

## Verifying the install

Two checks, in increasing order of confidence:

1. **The frontmatter is valid.** The official reference validator is the
   authority on whether a skill is portable. It is a Python package:

   ```bash
   pip install "git+https://github.com/agentskills/agentskills.git#subdirectory=skills-ref"
   skills-ref validate .agents/skills/analyzing-product-demo-ux
   # -> Valid skill: .agents/skills/analyzing-product-demo-ux
   ```

2. **The agent can see it.** Ask the agent which skills it has, or address one
   directly. In Claude Code, `/skills` lists them. If a skill is absent, the
   usual causes are a directory name that does not match the `name` in the
   frontmatter (the spec requires them to match) or an unrecognised frontmatter
   field.

## Frontmatter: why these skills install anywhere

Both skills declare only the six fields that the open specification defines:
`name`, `description`, `license`, `compatibility`, `metadata`, `allowed-tools`.
Claude Code accepts additional fields — `model`, `context`, `argument-hint` and
others — but a skill using them fails validation elsewhere and is rejected
outright by claude.ai and the Skills API, so this repository does not use them.
A test enforces it.

The practical consequence: these skills work in Claude Code, Claude Desktop,
ChatGPT and Codex, Cursor, VS Code, Gemini CLI, Zed, Goose, OpenCode and the
rest of the clients listed at [agentskills.io/clients](https://agentskills.io)
without modification.

If you fork these skills and want Claude Code-only features, put them in a
separate skill you never intend to upload — do not add the fields here.

## Using the skills without the MCP server

The skills are written to work on a FlowScope report regardless of how it
arrived. If the MCP server is not connected, an agent that has the report as a
file or as pasted text can still follow the operational guidance: read the
synthesis rather than re-deriving it from the raw transcript, cite the screen
behind each claim, and say "the demo does not show this" rather than inferring.

Produce a report without the MCP server by running the backend's own script:

```bash
cd backend
python scripts/export_analysis.py --data-dir ./data --out ./export
```

That writes one directory per video with `frames.json` and `flow.md`, which is
exactly the shape the skills expect.
