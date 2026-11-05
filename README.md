# sandro-claude-tooling

A curated [Claude Code](https://docs.claude.com/en/docs/claude-code) plugin
marketplace.

## Add this marketplace

```bash
claude plugin marketplace add sandrop/sandro-claude-tooling
```

## Browse and install plugins

```bash
claude plugin marketplace list
claude plugin install <plugin>@sandro-claude-tooling
```

## Plugins

| Plugin | Summary | Usage |
|---|---|---|
| `context-window-manager` | Session context-window handoff and resume: save session state to a per-repo `CONTEXT-*.md` file and resume from it with git-drift and stale-reference checks. | `/context-window-manager:handoff` (save state), `/context-window-manager:resume` (restore state) |

`jiranator` now ships from its own marketplace at
[`sandrop/jiranator`](https://github.com/sandrop/jiranator)
(`claude plugin marketplace add sandrop/jiranator`).
