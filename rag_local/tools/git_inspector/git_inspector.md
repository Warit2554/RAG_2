# Git Inspector Tool Specification

The Git Inspector Tool allows the LLM to inspect repository status, recent commits, and modifications safely.

## Components
- **run_git_command**: Executes specified git subcommands inside the workspace root.

## Security Controls
- Executions are strictly limited to the following read-only commands: `status`, `diff`, `log`, `show`, `branch`, `describe`.
- Mutable commands (e.g. `commit`, `push`, `reset`, `checkout`) are blocked.
- Advanced scripting/ext-diff arguments are blocked to prevent arbitrary command execution.
