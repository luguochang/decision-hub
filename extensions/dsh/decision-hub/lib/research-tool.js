import { s as g, f as y, w as N, o as T, x as I, y as V, n as z, l as Y, z as Z, A as ee, B as te, C as re, D as ae, E as K } from "./baseline-transport-CMVYRXyy.js";
import { defineTool as se } from "@deepseek-ai/dsh-tools";
import C from "@deepseek-ai/schemastery";
import { createHash as ie } from "node:crypto";
const b = g().min(1).max(128), L = y(g().regex(/^[a-z0-9.-]+$/).max(253)).max(64), B = y(N(["live", "replay"])).max(2), ne = T({
  schema_version: Y("research-query-catalog.v1"),
  catalog_hash: g().regex(/^[a-f0-9]{64}$/),
  scope: T({
    run_id: b,
    dsh_session_id: b,
    request_hash: g().regex(/^[a-f0-9]{64}$/),
    generation: z().int().positive(),
    status: N(["bound", "legacy_unbound"]),
    method_hash: g().regex(/^[a-f0-9]{64}$/).nullable(),
    execution_mode: N(["live", "replay"]).nullable(),
    // Frozen Method/Link ceiling, included in catalog_hash. Optional only for
    // older/unbound catalogs; managed-live execution requires it before POST.
    deadline_at: g().datetime({ offset: !0 }).nullable().optional()
  }).strict().optional(),
  capabilities: y(T({
    capability_id: b,
    allowed_domains: L,
    modes: B,
    routes: y(T({
      provider_id: b.nullable(),
      allowed_domains: L,
      fields: y(b).max(128).nullable(),
      modes: B,
      requires_event_window: I(),
      symbols: y(b).max(256).nullable().optional(),
      symbols_required: I().nullable().optional(),
      max_symbols: z().int().positive().nullable().optional(),
      symbol_fields: V(y(b).max(128)).nullable().optional(),
      sources: y(T({
        source_ref: b,
        domains: L,
        allowed_paths: y(g().max(2048)).max(128),
        requirement_ids: y(b).max(128),
        excluded_paths: y(g().regex(/^\/[^?#]*$/).max(2048)).max(128).optional(),
        match_paths_exactly: I().optional()
      }).strict()).max(256).nullable().optional(),
      target_url_required: I().nullable().optional(),
      evidence_ref_required: I().nullable().optional(),
      usage_notes: g().max(4096).nullable().optional()
    }).strict()).max(64)
  }).strict()).max(64)
}).strict(), M = 3e4;
function oe(e, t) {
  return new Promise((s, i) => {
    const r = () => {
      t.removeEventListener("abort", r), i(t.reason);
    };
    t.addEventListener("abort", r, { once: !0 }), t.aborted && r(), e.then((l) => {
      t.removeEventListener("abort", r), s(l);
    }, (l) => {
      t.removeEventListener("abort", r), i(l);
    });
  });
}
function F(e) {
  return Array.isArray(e) ? `[${e.map(F).join(",")}]` : e !== null && typeof e == "object" ? `{${Object.entries(e).sort(([t], [s]) => t < s ? -1 : t > s ? 1 : 0).map(([t, s]) => `${JSON.stringify(t)}:${F(s)}`).join(",")}}` : JSON.stringify(e);
}
async function ce(e) {
  const t = new URL(e.serviceUrl);
  if (!["http:", "https:"].includes(t.protocol) || !t.pathname.endsWith("/execute"))
    throw new Error("research_catalog_service_url_invalid");
  t.pathname = t.pathname.replace(/\/execute$/, "/catalog"), t.search = "", t.hash = "", e.sessionId && t.searchParams.set("research_session_id", e.sessionId);
  const s = e.catalogTimeoutMs ?? M;
  if (!Number.isSafeInteger(s) || s < 1 || s > M)
    throw new Error("research_catalog_budget_invalid");
  const i = new AbortController(), r = e.signal ? AbortSignal.any([e.signal, i.signal]) : i.signal, l = setTimeout(() => i.abort(), s);
  let a = "fetch";
  const n = async (u) => {
    try {
      r.throwIfAborted();
      const o = await oe(u(), r);
      return r.throwIfAborted(), o;
    } catch {
      const o = e.signal?.aborted ? "research_catalog_transport_cancelled" : i.signal.aborted ? "research_catalog_transport_timeout" : "research_catalog_transport_unavailable", p = o === "research_catalog_transport_timeout" ? ` deadline_ms=${s}` : "";
      throw new Error(`${o} phase=${a}${p}`);
    }
  };
  try {
    const u = await n(() => (e.fetchImpl ?? fetch)(t, {
      headers: { "x-decision-hub-bridge-key": e.authKey },
      signal: r,
      redirect: "error"
    }));
    if (!u.ok) throw new Error(`research_catalog_http_${u.status}`);
    a = "response_body";
    const o = await n(() => u.text());
    if (o.length > 64e3) throw new Error("research_catalog_too_large");
    const p = ne.parse(JSON.parse(o)), { catalog_hash: q, ...m } = p;
    if (ie("sha256").update(F(m)).digest("hex") !== q)
      throw new Error("research_catalog_hash_mismatch");
    if (e.sessionId && p.scope?.dsh_session_id !== e.sessionId)
      throw new Error("research_catalog_scope_identity_mismatch");
    if (p.scope?.status === "legacy_unbound" && p.capabilities.length !== 0)
      throw new Error("research_catalog_legacy_scope_invalid");
    return await n(() => Promise.resolve(p));
  } finally {
    clearTimeout(l);
  }
}
function le(e) {
  return " Gateway query catalog (filtered to the frozen Run when scope is present; not evidence or execution authorization): " + JSON.stringify(e) + " Use only capabilities allowed by this Run plan. fields are requested metric names, not response metadata such as venue, source_url or observed_at. Select fields supported together by one route; do not combine spot-only and derivatives-only field sets. fields=null in this catalog means unspecified, not arbitrary fields allowed. allowed_domains may narrow a route using its exact listed host or be empty for server selection; never replace an API host with its parent website domain. Keep requested symbols from the plan. Event-window routes require the existing event and offsets; Select symbols, fields and domains supported together by the SAME route. A symbols list is the adapter allowlist; null is unknown, not unrestricted. Honor symbols_required and max_symbols; never send [] when symbols are required. For historical datasets use symbol_fields for that exact dataset and an explicit window. For document URLs match the listed source domain, path AND requirement_id; If match_paths_exactly=true, allowed_paths are exact paths, never prefixes; otherwise their existing prefix semantics apply. Always reject paths in excluded_paths even when an allowed prefix would match. Do not drop either restriction. These catalog hints do not grant admission: the server source registry still validates every fetch. web.fetch and browser.readback may have different source scopes. Do not relabel the requirement to evade a rejection. Missing retained documents require a new approved fetch; never invent evidence_ref or content_ref. Follow usage_notes. current snapshots use event_id=null and requested_event_offsets=[]. A rejected request remains a failed attempt; correcting invalid input requires a new call and never authorizes widening permissions, changing PIT or relabeling evidence.";
}
const ye = "decision-hub-research-tool", fe = ["tools"], $ = "decision_hub_research", de = 65e3, G = 36e5, Q = 1e6;
function U(e) {
  let t = e;
  const s = /* @__PURE__ */ new Set();
  for (; ; ) {
    const i = String(t.id);
    if (s.has(i)) return i;
    s.add(i);
    const r = t.session?.header?.parentSession;
    if (typeof r != "string" || r.length === 0 || r === i)
      return i;
    const l = t.ctx?.agents?.get(r);
    if (l === void 0) return r;
    t = l;
  }
}
const be = C.object({
  serviceUrl: C.string(),
  authKey: C.string(),
  // Legacy/replay observation only. Managed live calls retain the original
  // Task deadline across preparation and durable admission; the Gateway's
  // individual capability deadlines remain unchanged.
  timeoutMs: C.natural().min(1).default(de),
  catalogTimeoutMs: C.natural().min(1).max(M).default(M)
}), W = {
  // Compatibility only. Identity belongs to the runtime, never to model text.
  request_id: {
    type: "string",
    description: "Omit this field. The runtime supplies the unique tool-call identifier."
  },
  capability_id: {
    type: "string",
    required: !0,
    description: "Use an exact capability_id listed in this tool query catalog and allowed by the research plan."
  },
  requirement_id: { type: "string", required: !0 },
  query: { type: "string", required: !0 },
  target_url: {
    oneOf: [{ type: "string" }, { type: "null" }],
    required: !0,
    description: "For web.fetch use the exact approved source URL. For market.* and data.historical use null; those adapters own their endpoint URLs."
  },
  symbols: { type: "array", items: { type: "string" }, required: !0 },
  fields: {
    type: "array",
    items: { type: "string" },
    required: !0,
    description: "Metric names supported together by a route in the tool query catalog. Do not request response metadata fields. For document fetch/readback use []."
  },
  allowed_domains: {
    type: "array",
    items: { type: "string" },
    required: !0,
    description: "Exact allowed API hosts from the query catalog, or [] for server-owned route selection. A parent website domain is not interchangeable with its API host."
  },
  max_results: {
    type: "integer",
    required: !0,
    description: "Number of evidence items, from 1 to 20. Historical candle count is determined by the requested date window, not this field."
  },
  max_cost_usd: { oneOf: [{ type: "number" }, { type: "null" }], required: !0 },
  event_id: { oneOf: [{ type: "string" }, { type: "null" }] },
  event_at: { oneOf: [{ type: "string" }, { type: "null" }] },
  window_start_at: { oneOf: [{ type: "string" }, { type: "null" }] },
  window_end_at: { oneOf: [{ type: "string" }, { type: "null" }] },
  requested_event_offsets: { type: "array", items: { type: "string" } },
  evidence_ref: { type: "string", description: "For document.readback, use the exact evidence_id already returned in this Session with the same requirement_id and retained original. Never invent a reference." },
  content_ref: { type: "string", description: "Optional: exact retained content_ref of that evidence; omit if unavailable. This is not a source URL." },
  round: { type: "integer", required: !0 },
  mode: { type: "string", enum: ["live", "replay"], required: !0 },
  observed_at: { type: "string", required: !0 },
  cutoff_at: { type: "string", required: !0 }
};
function X(e) {
  const t = new URL(e.serviceUrl);
  if (!["http:", "https:"].includes(t.protocol))
    throw new Error("decision_hub_research_service_url_invalid");
  if (e.authKey.length === 0) throw new Error("decision_hub_research_auth_key_missing");
  if (!Number.isInteger(e.timeoutMs) || e.timeoutMs < 1)
    throw new Error("decision_hub_research_timeout_invalid");
  const s = e.fetchImpl ?? fetch, i = e.queryCatalog?.scope?.status === "bound" && e.queryCatalog.scope.execution_mode === "live", r = ["mode", "round", "observed_at", "cutoff_at"], l = Object.fromEntries(Object.entries(W).filter(([a]) => !i || !r.includes(a)).map(([a, n]) => {
    const { required: u, ...o } = { required: void 0, ...n };
    return [a, e.catalogForAgent && r.includes(a) ? o : n];
  }));
  return se({
    name: $,
    description: "Execute one audited Decision Hub research capability. Session identity is supplied by the trusted DSH execution context; never include or infer a Session identifier." + (i ? " Online execution times, mode and round are supplied by the trusted gateway. Do not supply observed_at, cutoff_at, deadline_at, mode or round. For historical data, specify the requested historical window instead." : "") + (e.queryCatalog ? le(e.queryCatalog) : ""),
    parameters: e.queryCatalog?.scope && e.queryCatalog.capabilities.length > 0 ? { ...l, capability_id: {
      ...W.capability_id,
      enum: e.queryCatalog.capabilities.map((a) => a.capability_id)
    } } : l,
    output: {
      // Canonical Zod validation below is generated from the schema source. The
      // DSH output layer uses explicit JsonValue to avoid maintaining a second schema.
      schema: { type: "json" },
      render: (a, n) => [{ type: "text", text: JSON.stringify(n) }]
    },
    finalizeContent(a, n) {
      if (!n.isError)
        try {
          if (!K.safeParse(n.value).success) return;
          const o = JSON.stringify(n.value);
          return Buffer.byteLength(o, "utf8") > Q ? void 0 : [{ type: "text", text: o }];
        } catch {
          return;
        }
    },
    timeoutMs: e.catalogForAgent || i ? G : e.timeoutMs,
    async execute(a, n) {
      if (n.agent === void 0) throw new Error("decision_hub_research_agent_required");
      if (Object.prototype.hasOwnProperty.call(a, "research_session_id"))
        throw new Error("decision_hub_research_session_identity_forbidden");
      const u = a, o = String(n.callId);
      if (!o || o === "undefined")
        throw new Error("decision_hub_research_call_identity_required");
      const p = n.agent, q = Date.now(), m = e.catalogSnapshotForAgent?.(p) ?? e.queryCatalog;
      let c = q + (e.catalogForAgent || i ? G : e.timeoutMs), j = !1;
      const P = new AbortController(), f = AbortSignal.any([n.signal, P.signal]);
      let A;
      const w = () => new Error(n.signal.aborted ? "decision_hub_research_cancelled" : j ? "decision_hub_research_task_deadline_elapsed" : "decision_hub_research_transport_timeout"), k = () => {
        if (A !== void 0 && clearTimeout(A), n.signal.aborted || !Number.isFinite(c) || c <= Date.now()) throw w();
        A = setTimeout(() => P.abort(), c - Date.now());
      }, H = (d) => {
        if (d.scope && d.scope.dsh_session_id !== U(p))
          throw new Error("research_catalog_scope_identity_mismatch");
        if (d.scope?.status === "bound" && d.scope.execution_mode === "live") {
          const h = d.scope.deadline_at;
          if (typeof h != "string" || !/(?:Z|[+-]\d{2}:\d{2})$/.test(h) || !Number.isFinite(Date.parse(h))) throw new Error("decision_hub_research_task_deadline_invalid");
          c = Math.min(c, Date.parse(h)), j = !0;
        } else c = Math.min(c, q + e.timeoutMs);
        k();
      }, D = async (d) => {
        let h;
        try {
          if (f.aborted || Date.now() >= c) throw w();
          const S = new Promise((O, x) => {
            h = () => x(w()), f.addEventListener("abort", h, { once: !0 });
          }), v = await Promise.race([d(), S]);
          if (f.aborted || Date.now() >= c) throw w();
          return v;
        } catch (S) {
          throw f.aborted || Date.now() >= c ? w() : S;
        } finally {
          h && f.removeEventListener("abort", h);
        }
      };
      try {
        m ? H(m) : k();
        const d = e.catalogForAgent ? await D(() => e.catalogForAgent(p, f)) : m;
        if (d) {
          if (m?.scope && d.scope && (m.scope.run_id !== d.scope.run_id || m.scope.request_hash !== d.scope.request_hash || m.scope.generation !== d.scope.generation || m.scope.deadline_at !== d.scope.deadline_at)) throw new Error("research_catalog_scope_identity_mismatch");
          H(d);
        }
        const h = d?.scope?.status === "bound" && d.scope.execution_mode === "live", S = h ? Object.fromEntries(Object.entries(u).filter(
          ([_]) => !["mode", "round", "observed_at", "requested_observed_at", "cutoff_at", "deadline_at"].includes(_)
        )) : u, v = (h ? Z : ee).parse({
          ...S,
          schema_version: h ? "research-capability-invocation.v1" : "research-capability-query.v1",
          request_id: o,
          research_session_id: U(p)
        }), O = new URL(t);
        h && (O.pathname = O.pathname.replace(/\/execute$/, "/invoke-live"));
        let x, E;
        if (h && !e.fetchImpl) {
          E = 0;
          try {
            x = await te({
              url: O,
              authKey: e.authKey,
              body: JSON.stringify(v),
              signal: f,
              deadlineAtMs: c,
              allowErrorResponse: !0,
              onStatus: (_) => {
                E = _;
              }
            });
          } catch (_) {
            throw f.aborted || Date.now() >= c ? w() : new Error(_ instanceof re ? _.code.replace(/^research_baseline_/, "decision_hub_research_") : "decision_hub_research_transport_unavailable");
          }
        } else {
          const _ = await D(() => s(O, {
            method: "POST",
            headers: { "content-type": "application/json", "x-decision-hub-bridge-key": e.authKey },
            body: JSON.stringify(v),
            signal: f,
            redirect: "error"
          }));
          E = _.status, x = await D(() => _.text());
        }
        if (f.aborted || Date.now() >= c) throw w();
        if (Buffer.byteLength(x, "utf8") > Q) throw new Error("decision_hub_research_oversize");
        const J = JSON.parse(x);
        if (E < 200 || E >= 300) {
          const _ = ae.safeParse(J);
          throw new Error(_.success ? `${_.data.error_code} provenance=${JSON.stringify(_.data)}` : `decision_hub_research_http_${E}`);
        }
        const R = K.parse(J);
        if (R.request_id !== v.request_id || R.capability_id !== v.capability_id || R.evidence_candidates.some((_) => _.requirement_id !== v.requirement_id))
          throw new Error("decision_hub_research_result_identity_mismatch");
        return R;
      } finally {
        A !== void 0 && clearTimeout(A);
      }
    }
  });
}
function ue(e, t) {
  const s = /* @__PURE__ */ new WeakMap(), i = async (r, l) => {
    l?.throwIfAborted();
    let a = s.get(r);
    if (!a) {
      if (a = await ce({ ...t, signal: l, sessionId: U(r) }), a.capabilities.length === 0) throw new Error("research_catalog_scope_empty");
      s.set(r, a);
    }
    return a;
  };
  e.tools.register(X({
    ...t,
    catalogForAgent: i,
    catalogSnapshotForAgent: (r) => s.get(r)
  })), e.on("system-prompt/assemble", async (r, { agent: l, signal: a }, n) => {
    const u = await n();
    if (!l || !u.tools.some((c) => c.name === $)) return u;
    const o = await i(l, a), { name: p, description: q, parameters: m } = X({ ...t, queryCatalog: o });
    return { ...u, tools: u.tools.map((c) => c.name === $ ? { name: p, description: q, parameters: m } : c) };
  });
}
async function ge(e, t) {
  ue(e, t);
}
export {
  be as Config,
  de as DEFAULT_RESEARCH_TOOL_TIMEOUT_MS,
  Q as MAX_CANONICAL_RESEARCH_RESULT_BYTES,
  $ as RESEARCH_TOOL_NAME,
  ge as apply,
  X as createResearchToolDefinition,
  fe as inject,
  ue as installScopedResearchCatalog,
  ye as name,
  U as resolveResearchSessionId
};
//# sourceMappingURL=research-tool.js.map
