# MCP Servers

Claude Code can reach an engine or an art tool through an MCP server: MCP for
Unity drives the open Unity editor, and servers exist for Blender, GIMP,
ComfyUI and others. The studio does not install or name any of them. It makes
sure the agents that need them can reach them, and gives Unity a ready route.

## Which agents see them

A subagent with a fixed `tools:` list sees no MCP tool at all. The agents whose
work lands in the engine or its assets therefore have no `tools:` list: they
inherit every tool of the session, the MCP servers included. Those without
`Agent` in their old list withhold it with `disallowedTools: Agent`.

- Engine specialists: `godot-*`, `unity-*`, `unreal-specialist`, `ue-gas-specialist`,
  `ue-replication-specialist`, `ue-umg-specialist`
- Programmers: `gameplay-programmer`, `engine-programmer`, `ui-programmer`,
  `ai-programmer`, `tools-programmer`
- Art and verification: `technical-artist`, `performance-analyst`, `prototyper`,
  `qa-tester`

Every other agent keeps its fixed list. To give one of them a single server,
add `mcp__<server>__*` to its `tools:` line; to keep a server from an inheriting
agent, add `mcp__<server>` to its `disallowedTools:`.

## Unity

Install [MCP for Unity](https://github.com/CoplayDev/unity-mcp) in the project and
start its server on the HTTP transport. Two routes then work:

- **The agent calls the tools directly**, for anything the editor can do.
- **`.claude/scripts/unity-mcp.py`** runs fixed steps from the shell: `compile`,
  `test`, `play`, `stop`, `console`, `shot`, `smoke`. The engine table's Unity row
  uses it for `run`, `test` and `smoke`, and `run-and-observe.md` uses it to look at
  the open editor. `--help` lists the steps; `$UNITY_MCP_URL` overrides the default
  `http://127.0.0.1:8080/mcp`.

`settings.json` allows the script and the MCP for Unity tools that only read,
compile or run tests, under both names the package registers: `UnityMCP` (its
Configure button for Claude Code runs `claude mcp add ... UnityMCP`) and `unityMCP`
(the clients it configures through a JSON file). Tools that change scenes, assets
or code still ask. If you registered the server under another name, the allow
entries do not match: copy them into `.claude/settings.local.json` with your name.
The agent tools are then `mcp__UnityMCP__*` or `mcp__unityMCP__*`.

## Your own servers

Art and audio servers (Blender, GIMP, ComfyUI and the like) are named by whoever
registers them, so the shared settings cannot allow them. Allow them per machine
in `.claude/settings.local.json`, which git ignores:

```json
{
  "permissions": {
    "allow": ["mcp__<server>"]
  }
}
```

`mcp__<server>` allows every tool of that server. Allow single tools
(`mcp__<server>__<tool>`) instead when a server can delete or overwrite files you
care about.
