# Research — Leveraging MCP, Multi-Agent & Parallel Processing in Aethermind

**Date:** 2026-07-29 · Research only (no build). Sources at bottom.
**Question:** How can Aethermind lean heavily on MCP, multiple agents, parallel processing, connectors, and global setups?

---

## 1. State of MCP (as of 2026-07-28 spec)

MCP just shipped its largest revision since launch (release candidate dated 2026‑07‑28). The parts that matter for us:

- **Stateless transport.** Protocol-level sessions and `Mcp-Session-Id` are gone. Any request can be answered by any server instance behind ordinary HTTP load balancers → **horizontal scaling for free**. This fits Aethermind's stateless FastAPI backend perfectly.
- **Routable headers.** Every request carries `Mcp-Method`; `Mcp-Name` on `tools/call` / `resources/read` / `prompts/get`. A gateway can route without parsing the body.
- **Caching.** `tools/list` and resource reads carry `ttlMs` + `cacheScope` (like HTTP Cache-Control) — clients know how long a tool list is fresh and whether it's shareable across users.
- **Tasks primitive (SEP-1686, experimental).** Async agent-to-agent communication with **retry semantics + result-expiry** — the standards-track answer to long-running/parallel jobs (exactly our ~10s extraction problem).
- **Authorization hardening.** DPoP (SEP-1932) and Workload Identity Federation (SEP-1933) under review; enterprise focus on audit trails + SSO + gateways.
- **Ecosystem scale.** 200+ official server implementations; registries hold tens of thousands (Glama ~62k, mcp.so ~20k). Remote hosted servers now include Slack, Atlassian, Linear, HubSpot, Sentry, Neon (Postgres), Vercel, AWS, Snowflake.

**Transports:** `stdio` (local subprocess), **Streamable HTTP** (remote, now stateless — the one to use), legacy SSE (deprecated).

---

## 2. Three ways Aethermind uses MCP

### A. Aethermind as MCP **client** — consume connectors (ingestion + output)
Stop hand-building every integration; plug into the connector ecosystem instead.

| Direction | Connector (MCP server) | Replaces backlog item |
|-----------|------------------------|------------------------|
| **Ingest** | Google Drive, Gmail, Slack, S3/R2 → auto-pull incoming documents | **B10** email-inbox ingestion (highest adoption ROI) |
| **Persist** | Postgres (Neon remote MCP) / object storage | **B6** persistent storage |
| **Push out** | ERP / accounting (QuickBooks, NetSuite, Stripe), generic webhook | **B10** ERP push |
| **Enrich** | Web search / vendor-registry / GSTIN validation | new — real cross-checks |

One MCP client layer → many systems, without writing a bespoke API client per vendor.

### B. Aethermind as MCP **server** — expose extraction as tools (strategic)
Wrap our own capability as MCP tools so **other people's agents** call us:
`extract_document`, `get_field_confidence`, `list_document_types`, `submit_correction`.
- Turns Aethermind from an app into **a connector others plug into** — a client's own Claude/agent, or their internal automation, calls Aethermind directly.
- The new **stateless Streamable HTTP** transport maps cleanly onto our stateless FastAPI and scales horizontally on Render/any cloud.
- This is the highest-leverage, most differentiated MCP play.

### C. Real multi-agent + parallel pipeline (make the "6 agents" real)
Replace the single synchronous OpenAI call + cosmetic agent UI with an actual pipeline. Research (MADP, financial-doc benchmarks) converges on two patterns:

- **Sequential pipeline** — each agent gets full accumulated context. Simple, but errors propagate one-way and tokens accumulate.
- **Parallel fan-out + merge** — independent extractors run **concurrently**, a **reconciliation agent** merges. Lower latency, isolates failures. ← the one that fixes our ~10s wait.

Concrete Aethermind pipeline:
```
Classifier (doc type)  → B8
   → Splitter (multi-doc / multi-page)
       → ║ parallel fan-out: extractor per field-group / per page ║
           → Reconciliation/merge agent
               → Grounding agent            (already built)
               → Validator / anomaly agent  → anomaly-detection backlog
                   → Human-in-the-loop review (already built)
```
MADP's version of this (Classificator, Splitter, Parser, Extraction, Validator + HITL) hit a **97% automation rate**. This is the blueprint for turning the fake agents into real ones and reaching straight-through processing (B9).

---

## 3. Multi-agent & parallel — the build tooling

- **Claude Agent SDK** (Python/TS; renamed from Claude Code SDK, Sep 2025) is the production harness — **in-process MCP servers**, lifecycle hooks, **subagents** (isolated context, own tools, own model). Framework comparisons rate it the strongest **MCP-native** agent SDK. This is what we'd productionize pipeline (C) on.
- **Dynamic Workflows** (June 2026): a lead agent fans out **tens–hundreds** of parallel subagents in one session — our per-page/per-field fan-out.
- **Performance Outcomes**: a separate **grader** loops each subagent until output meets a rubric — automates our grounding/validation retry loop.
- Anthropic reports multi-agent beating single-agent by **up to ~90%** on their benchmarks (vendor figure — treat as directional).
- We already use this primitive: the **Superpowers subagent-driven workflow** (fresh implementer + reviewer per task) is the same fan-out/grader pattern applied to *building* Aethermind.

---

## 4. Global setup (dev + prod)

**Dev loop (Claude Code MCP, user scope = global across all projects):**
```
claude mcp add --transport http <name> --scope user <url>     # remote SaaS
claude mcp add --scope user <name> -- <cmd>                    # local stdio
```
- **Scopes:** `project` (`.mcp.json`, committed, team-shared) · `user` (`~/.claude.json`, global to you) · `organization`.
- Useful dev connectors: Postgres/Neon, GitHub, Filesystem, Sentry.

**Prod:** Agent SDK loads MCP servers in-process; secrets/auth per connector; stateless HTTP servers sit behind a normal load balancer + gateway (the 2026 spec's whole point).

---

## 5. Risks / caveats (must design for)

- **Prompt-injection surface.** Every MCP tool call over untrusted document content is an injection vector — directly relevant to our grounding/injection-defense work. Documents are untrusted input; tool outputs are data, not instructions.
- **Secrets & authz.** Each connector needs scoped credentials; lean on the new DPoP / Workload Identity Federation direction, not long-lived keys.
- **Cost/latency.** Parallel fan-out = many concurrent LLM calls. Needs a cost-guardian and concurrency cap.
- **Spec is a July‑2026 RC.** Stateless transport is new; pin versions, expect churn. Tasks primitive is still experimental.
- **The ~90% figure is a vendor claim.** Validate on our own docs before quoting it to clients.

---

## 6. How this maps to the existing backlog (`docs/TODO.md`)

MCP is not a new backlog item — it's the **implementation strategy** for several existing ones:
- **B6** persistent storage → Postgres/Neon **via MCP**
- **B8** auto-classification → Classifier **agent** in pipeline (C)
- **B9** straight-through processing → parallel pipeline + validator reaching high automation
- **B10** integrations (email ingest + ERP push) → **connectors** (section 2A)
- async processing → **parallel fan-out** (2C) / Tasks primitive
- anomaly detection → **Validator agent** (2C)
- Q7 "relabel fake agents" → superseded: make them **real** (2C)

**Suggested first concrete step when we resume:** a thin **MCP-client ingestion spike** — pull a document from Google Drive (or Gmail) via its MCP server into the existing `/upload` path. Smallest slice that proves the connector model end-to-end and doubles as the B10 foundation. Second: expose `extract_document` as an MCP **server** tool (play B).

---

## Sources
- MCP 2026-07-28 spec release candidate — https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/
- 2026 MCP Roadmap — https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/
- MCP stateless/routable-headers analysis — https://4sysops.com/archives/2026-07-28-model-context-protocol-mcp-stateless-multi-round-trip-routable-headers-authorization-hardening/
- Claude Agent SDK / subagents / Dynamic Workflows — https://www.developersdigest.tech/blog/claude-code-agent-teams-subagents-2026 · https://www.mindstudio.ai/blog/code-with-claude-2026-new-agent-features
- MCP registries & connector lists — https://glama.ai/mcp/servers · https://tokenmix.ai/blog/mcp-servers-list-2026-complete-directory
- Claude Code MCP setup/scopes — https://dev.to/stravukarl/claude-code-mcp-setup-a-practical-2026-guide-424f
- Multi-agent document pipelines (MADP, 97% automation) — https://arxiv.org/pdf/2605.17159 · https://github.com/aws-solutions-library-samples/accelerated-intelligent-document-processing-on-aws/blob/main/docs/custom-MCP-agent.md · https://www.llamaindex.ai/blog/extending-the-llamaparse-mcp-for-more-document-processing-power
