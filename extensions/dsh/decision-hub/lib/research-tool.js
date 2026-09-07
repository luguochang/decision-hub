import { t as d, u as y, v as _ } from "./index-CWciLEwK.js";
import { defineTool as p } from "@deepseek-ai/dsh-tools";
import i from "@deepseek-ai/schemastery";
const w = "decision-hub-research-tool", v = ["tools"], l = "decision_hub_research", h = 25e3, O = i.object({
  serviceUrl: i.string(),
  authKey: i.string(),
  // The Gateway owns the 20s capability deadline. Keep a small transport grace
  // so its typed ErrorProvenance reaches DSH before the wrapper times out.
  timeoutMs: i.natural().min(1).default(h)
}), m = {
  request_id: { type: "string", required: !0 },
  capability_id: { type: "string", required: !0 },
  requirement_id: { type: "string", required: !0 },
  query: { type: "string", required: !0 },
  target_url: { oneOf: [{ type: "string" }, { type: "null" }], required: !0 },
  symbols: { type: "array", items: { type: "string" }, required: !0 },
  fields: { type: "array", items: { type: "string" }, required: !0 },
  allowed_domains: { type: "array", items: { type: "string" }, required: !0 },
  max_results: { type: "integer", required: !0 },
  max_cost_usd: { oneOf: [{ type: "number" }, { type: "null" }], required: !0 },
  event_id: { oneOf: [{ type: "string" }, { type: "null" }] },
  event_at: { oneOf: [{ type: "string" }, { type: "null" }] },
  window_start_at: { oneOf: [{ type: "string" }, { type: "null" }] },
  window_end_at: { oneOf: [{ type: "string" }, { type: "null" }] },
  requested_event_offsets: { type: "array", items: { type: "string" } },
  round: { type: "integer", required: !0 },
  mode: { type: "string", enum: ["live", "replay"], required: !0 },
  observed_at: { type: "string", required: !0 },
  cutoff_at: { type: "string", required: !0 }
};
function g(e) {
  const t = new URL(e.serviceUrl);
  if (!["http:", "https:"].includes(t.protocol))
    throw new Error("decision_hub_research_service_url_invalid");
  if (e.authKey.length === 0) throw new Error("decision_hub_research_auth_key_missing");
  if (!Number.isInteger(e.timeoutMs) || e.timeoutMs < 1)
    throw new Error("decision_hub_research_timeout_invalid");
  const u = e.fetchImpl ?? fetch;
  return p({
    name: l,
    description: "Execute one audited Decision Hub research capability. Session identity is supplied by the trusted DSH execution context; never include or infer a Session identifier.",
    parameters: m,
    output: {
      // Canonical Zod validation below is generated from the schema source. The
      // DSH output layer uses explicit JsonValue to avoid maintaining a second schema.
      schema: { type: "json" },
      render: (s, r) => [{ type: "text", text: JSON.stringify(r) }]
    },
    timeoutMs: e.timeoutMs,
    async execute(s, r) {
      if (r.agent === void 0) throw new Error("decision_hub_research_agent_required");
      if (Object.prototype.hasOwnProperty.call(s, "research_session_id"))
        throw new Error("decision_hub_research_session_identity_forbidden");
      const c = d.parse({
        schema_version: "research-capability-query.v1",
        ...s,
        research_session_id: r.agent.id
      }), n = await u(t, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-decision-hub-bridge-key": e.authKey
        },
        body: JSON.stringify(c),
        signal: AbortSignal.any([r.signal, AbortSignal.timeout(e.timeoutMs)])
      }), a = await n.json();
      if (!n.ok) {
        const o = y.safeParse(a);
        throw new Error(
          o.success ? `${o.data.error_code} provenance=${JSON.stringify(o.data)}` : `decision_hub_research_http_${n.status}`
        );
      }
      return _.parse(a);
    }
  });
}
function S(e, t) {
  e.tools.register(g(t));
}
export {
  O as Config,
  h as DEFAULT_RESEARCH_TOOL_TIMEOUT_MS,
  l as RESEARCH_TOOL_NAME,
  S as apply,
  g as createResearchToolDefinition,
  v as inject,
  w as name
};
//# sourceMappingURL=research-tool.js.map
