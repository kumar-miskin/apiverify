# Distribution status and first-user plan

## Shipped (verified 2026-11-26)
v0.1.0 has known incorrect verdicts (see README warning). Do not promote until the fixed release passes independent re-test.

- GitHub (public, MIT): https://github.com/kumar-miskin/apiverify
- PyPI: https://pypi.org/project/apiverify/0.1.0/  (`pip install apiverify`; MCP: `uvx --from apiverify apiverify-mcp`)
- Official MCP registry: `io.github.kumar-miskin/apiverify` v0.1.0, status active
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
