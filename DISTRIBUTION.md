# How agents could find and use this (proposed, nothing here is shipped)

Agents choose tools from what is in their config, their instructions file, and the
registries their client browses. So the order is about where a developer would
already look.

1. Repo quality first: README with a 3-line install, the three-answer contract, and
   the limits. Done in this repo.
2. MCP: the server already speaks stdio. Next steps, each needing the owner's go-ahead:
   publish to PyPI as `apiverify` (so `uvx apiverify-mcp` works with no clone), then list
   in the official MCP registry and in client-side directories.
3. Instructions-file snippet: one pasteable line for AGENTS.md / CLAUDE.md / Cursor
   rules so an agent calls the tool before using an uncertain API. This is what makes
   a tool get called, not just installed.
4. A hosted endpoint only if local use shows demand. Hosting adds cost, abuse limits
   and a trust question (artifact sha256 is already returned for audit).
5. Evidence before promotion: run the agent comparison (no tool vs Context7 vs
   apiverify) on version-specific questions and publish the numbers, including losses.
   Without that, a launch post would overclaim.
