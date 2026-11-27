# Distribution status and first-user plan

## Shipped (verified 2026-11-26)
Latest is 0.1.3 (cleared by an independent evaluator on 86 synthetic probes, 0 wrong; see README for limits). 0.1.0, 0.1.1 and 0.1.2 are marked deprecated in the MCP registry (0.1.2 has known wrong answers fixed in 0.1.3). Library batches: 13 of 14 probed with 0 wrong; pyparsing timed out and jinja2 2.11.3 could not be probed (our own runs). No reliability or agent-benefit claims are made; see README.

- GitHub (public, MIT): https://github.com/kumar-miskin/apiverify
- PyPI: https://pypi.org/project/apiverify/0.1.3/  (`pip install apiverify`; MCP: `uvx --from apiverify apiverify-mcp`; if uv reports no matching version right after a release, add `--no-cache`)
- Official MCP registry: `io.github.kumar-miskin/apiverify` v0.1.3 active and latest; v0.1.0, v0.1.1 and v0.1.2 deprecated
  (`curl 'https://registry.modelcontextprotocol.io/v0/servers?search=apiverify'`)

Being listed is not agent selection. Agents only call tools that are in their config or
instructions. A registry entry makes the server findable by a developer or by a client
that browses the registry. It does not make any agent use it.

## Demo (real output, checked after publishing)
    apiverify requests.exceptions.JSONDecodeError --package requests --version 2.26.0   -> not_found
    apiverify requests.exceptions.JSONDecodeError --package requests --version 2.27.0   -> verified
An agent writing against requests 2.26 that imports JSONDecodeError would hit ImportError;
this answers that from the release's own source, with the artifact sha256.

## Proposed first-user plan (NOT done, no outreach made)
1. Instructions snippet for AGENTS.md / CLAUDE.md / Cursor rules: "Before using an API you
   are unsure exists in the pinned version, call verify_python_api." This is what gets a
   tool called.
2. Use it ourselves on real pinned-version failures (issues where an agent used a symbol
   missing in the installed version) and record hits and misses.
3. Only after an independent evaluation (no tool vs Context7 vs apiverify, on
   version-specific questions) share numbers, including losses. No benchmark exists yet.
4. A hosted endpoint only if local use shows demand.

## Not measured
Kwarg and deprecation signals are unscored. No Context7 comparison. No evidence yet that
anyone would pay.
