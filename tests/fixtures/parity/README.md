# Port Parity Fixtures

These fixtures are the contract for the Node/TypeScript runtime.

They are intentionally small. The fixture builder creates temp homes from the
manifest so parity tests can mutate isolated directories without touching real
`~/.agents`, `~/.claude`, or `~/.codex` state.

Use these fixtures to verify:

1. TypeScript implementation output.
2. Filesystem post-state.
3. Action-log behavior.
4. Rollback behavior.

The authoritative fixture index is `manifest.json`.
