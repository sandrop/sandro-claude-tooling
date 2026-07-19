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
| `context-window-manager` | Session context-window handoff and resume: save session state to a per-repo `CONTEXT-*.md` file and resume from it with git-drift and stale-reference checks. | `context-window-handoff` (save state), `context-window-resume` (restore state) |

Every new plugin **must add a row** here as part of shipping it. More plugins
are on the way.
