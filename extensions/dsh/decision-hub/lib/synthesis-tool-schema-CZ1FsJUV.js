import { parameterSchemaSpecToJsonSchema as I } from "@deepseek-ai/dsh-tools";
import { createHash as D } from "node:crypto";
import { H as k, I as L, J as N, K as M, L as T, M as Z, N as A, O as C, P as R, Q as P, R as U, F as J, S as j, T as B } from "./baseline-transport-CMVYRXyy.js";
function g(e, i = !1) {
  if (e instanceof k) return g(e.innerType(), i);
  if (e instanceof L) return g(e.unwrap(), i);
  if (e instanceof N) {
    if (e._def.unknownKeys !== "strict") throw new Error("synthesis_schema_projection_open_object");
    const t = {};
    for (const [n, a] of Object.entries(e.shape))
      t[n] = { ...g(a, i), ...a.isOptional() ? {} : { required: !0 } };
    return { type: "object", properties: t, additionalProperties: !1 };
  }
  if (e instanceof M) {
    const t = [
      e._def.minLength && `Minimum items: ${e._def.minLength.value}.`,
      e._def.maxLength && `Maximum items: ${e._def.maxLength.value}.`
    ].filter(Boolean).join(" ");
    return { type: "array", items: g(e.element, i), ...t ? { description: t } : {} };
  }
  if (e instanceof T)
    return { oneOf: e.options.map((n) => g(n, i)) };
  if (e instanceof Z) return { type: "string", enum: e.options };
  if (e instanceof A && typeof e.value == "string") return { type: "string", const: e.value };
  if (e instanceof A && typeof e.value == "number") return { type: "number", const: e.value };
  if (e instanceof A && typeof e.value == "boolean") return { type: "boolean", const: e.value };
  if (e instanceof C) return { type: "boolean" };
  if (e instanceof R) return { type: "null" };
  if (e instanceof P) {
    const t = e._def.checks.map((n) => n.kind === "min" ? `Minimum length: ${n.value}.` : n.kind === "datetime" ? "RFC3339 datetime with timezone required." : i && n.kind === "regex" ? `Must match regex: ${n.regex.source}.` : i && n.kind === "max" ? `Maximum length: ${n.value}.` : "").filter(Boolean).join(" ");
    return { type: "string", ...t ? { description: t } : {} };
  }
  if (e instanceof U) {
    const t = e._def.checks.map((n) => n.kind === "min" ? `Minimum: ${n.value}.` : n.kind === "max" ? `Maximum: ${n.value}.` : "").filter(Boolean).join(" ");
    return { type: "number", ...t ? { description: t } : {} };
  }
  throw new Error(`synthesis_schema_projection_unsupported:${e._def.typeName}`);
}
const W = g(J);
function Y(e) {
  const i = I({ candidate: { ...g(B, !0), required: !0 } }), t = i.properties.candidate, n = t.properties?.action_intents?.items?.oneOf, a = t.properties?.action_intents, c = t.properties?.mechanism_reviews?.items;
  if (!n?.length || !c?.properties) throw new Error("synthesis_schema_projection_decision_shape_invalid");
  const l = "Before the first qualified freeze, next_review_at must be after the actual synthesis submission time and no later than the fixed window expiry; a timestamp that was future at evidence collection may become due during corrections. Do not advance as_of to hide stale inputs. After the single review, resubmit the exact frozen bytes; a due next_review_at alone is not expiry and must not be rewritten.";
  a && (a.description = `${a.description ?? ""} For EVERY declared research_candidate_v1.horizons horizon, provide separate flat and long branches for the scope (normally six for 30m/24h/72h). Never delete Action branches to bypass an error. Missing evidence remains an explicit branch with its actual gap, not a fabricated fact.`.trim());
  for (const d of n) {
    const y = d.properties?.as_of, b = d.properties?.mechanism_review_ref;
    if (!y || !b) throw new Error("synthesis_schema_projection_action_binding_missing");
    y.description = `${y.description ?? ""} Actual information cutoff; ALL actions and mechanisms in the wrapper share this same instant. This is not an individual price observed_at or the acquisition/execution deadline. All supporting inputs must already be available.`.trim(), b.description = `${b.description ?? ""} Exact review_id of a supplied mechanism with the same scope_id, horizon and cutoff=action.as_of. Never use none or delete a branch to bypass a consistency error.`.trim();
    const w = d.properties?.next_review_at;
    if (!w) throw new Error("synthesis_schema_projection_next_review_missing");
    w.description = `${w.description ?? ""} ${l}`.trim();
    for (const [$, x] of [["entry", ["entry"]], ["exit", ["exit"]], ["invalidation", ["invalidation"]]]) {
      const S = d.properties?.[$]?.items?.properties?.purpose;
      if (!S) throw new Error("synthesis_schema_projection_condition_shape_invalid");
      S.enum = [...x], S.description = `${$} condition purpose must be ${x.join(" or ")}. The current frozen Action catalog does not admit risk_upgrade as a purpose; describe that risk in original_text and use purpose exit.`;
    }
  }
  const f = t.properties?.research_candidate_v1?.properties?.horizons?.items?.properties?.next_review_at;
  if (!f) throw new Error("synthesis_schema_projection_horizon_review_missing");
  f.description = `${f.description ?? ""} ${l}`.trim();
  const r = c.properties.cutoff;
  if (!r) throw new Error("synthesis_schema_projection_mechanism_cutoff_missing");
  r.description = `${r.description ?? ""} Actual information cutoff shared across ALL action.as_of and mechanism.cutoff fields in the wrapper. Market observed_at remains a fact field and deadlines are scheduling limits, not evidence availability.`.trim();
  const o = "Exact admitted Evidence IDs from the frozen input or a successful research result, never requirement IDs, metric names, source names, node/edge/review IDs or ClaimSignal IDs. Examples of INVALID IDs: derivatives_crowding, market_regime_context, real_yield_10y. An absent metric is an unresolved question, not evidence; use an empty array when no admitted evidence exists and describe the missing observation in mechanism.unresolved. Do not replace missing refs with a convenient unrelated Evidence ID.", _ = (d) => {
    if (!d) throw new Error("synthesis_schema_projection_evidence_shape_invalid");
    d.description = `${d.description ?? ""} ${o}`.trim();
  };
  for (const d of n) _(d.properties?.evidence_refs);
  const u = c.properties.edges?.items?.properties;
  _(u?.support_refs), _(u?.counter_refs), _(c.properties.priced_in_refs), _(c.properties.alternative_refs);
  const h = c.properties.nodes?.items?.properties?.refs;
  if (!h) throw new Error("synthesis_schema_projection_node_refs_missing");
  h.description = `${h.description ?? ""} Cite exact admitted Evidence IDs or exact ClaimSignal IDs supplied by this request; a ClaimSignal remains an attributed opinion. Requirement/metric names are not references. Put missing observations in unresolved.`.trim();
  const s = c.properties.unresolved;
  if (!s) throw new Error("synthesis_schema_projection_unresolved_missing");
  s.description = `${s.description ?? ""} Describe missing observations and untested alternatives here, including requirement/metric names when useful. These are questions, not authenticated evidence; never copy them into support_refs, counter_refs, alternative_refs or priced_in_refs.`.trim();
  const m = c.properties.alternative_refs;
  if (!m) throw new Error("synthesis_schema_projection_mechanism_shape_invalid");
  m.description = `${m.description ?? ""} Admitted independent Evidence IDs supporting alternative explanations; not causal-link IDs, node IDs, edge IDs, review IDs or ClaimSignal IDs. Use an empty array when no admitted evidence supports an alternative and describe the gap in unresolved.`.trim();
  const O = {}, E = (d) => {
    if (Array.isArray(d)) return d.map(E);
    if (d === null || typeof d != "object") return d;
    const y = Object.fromEntries(Object.entries(d).map(([$, x]) => [$, E(x)])), b = JSON.stringify(y);
    if (b.length < 200 || !["type", "oneOf", "anyOf", "allOf"].some(($) => $ in y)) return y;
    const w = `synthesis_${D("sha256").update(b).digest("hex").slice(0, 20)}`;
    if (O[w] !== void 0 && JSON.stringify(O[w]) !== b) throw new Error("synthesis_schema_intern_collision");
    return O[w] = y, { $ref: `#/$defs/${w}` };
  }, q = E(i.properties.candidate), z = e.properties.candidate;
  return {
    ...e,
    properties: { candidate: {
      oneOf: [z, q],
      description: "Use exactly the protocol selected by the frozen request.output_schema_ref. Legacy result ref requires the old candidate; research_signal.schema.yaml#/$defs/research_decision_synthesis_v1 requires the COMPLETE new wrapper. No model-selected fallback is accepted."
    } },
    $defs: O
  };
}
function p(e) {
  return e !== null && typeof e == "object" && !Array.isArray(e);
}
function v(e, i) {
  return Object.fromEntries(Object.entries(e).map(([t, n]) => (t === "properties" || t === "$defs") && p(n) ? [t, Object.fromEntries(Object.entries(n).map(([a, c]) => [a, i(c)]))] : ["items", "additionalProperties", "if", "then", "else", "not"].includes(t) ? [t, Array.isArray(n) ? n.map(i) : i(n)] : ["oneOf", "anyOf", "allOf"].includes(t) && Array.isArray(n) ? [t, n.map(i)] : [t, n]));
}
function F(e) {
  const i = p(e.$defs) ? e.$defs : {}, t = (a, c = /* @__PURE__ */ new Set()) => {
    if (!p(a)) return a;
    if (typeof a.$ref == "string") {
      const r = a.$ref.slice(8);
      if (!a.$ref.startsWith("#/$defs/") || !Object.hasOwn(i, r) || c.has(r)) throw new Error("synthesis_schema_local_ref_invalid");
      const o = t(i[r], /* @__PURE__ */ new Set([...c, r]));
      if (!p(o)) throw new Error("synthesis_schema_local_ref_invalid");
      const { $ref: _, $defs: u, ...h } = a, s = v(h, (m) => t(m, c));
      return Object.keys(s).length ? Object.keys(s).some((m) => Object.hasOwn(o, m)) ? { allOf: [o, s] } : { ...o, ...s } : o;
    }
    const { $defs: l, ...f } = a;
    return v(f, (r) => t(r, c));
  }, n = t(e);
  if (!p(n)) throw new Error("synthesis_schema_projection_decision_shape_invalid");
  return n;
}
function V(e, i) {
  let t = {};
  const n = (r) => {
    if (!p(r)) return r;
    if (typeof r.$ref == "string") {
      const o = r.$ref.slice(8);
      if (!Object.hasOwn(i, o)) throw new Error("synthesis_schema_local_ref_invalid");
      Object.hasOwn(t, o) || (t[o] = i[o], n(i[o]));
    } else v(r, n);
    return r;
  };
  n(e);
  let a = e;
  for (; ; ) {
    const r = /* @__PURE__ */ new Map(), o = (s) => (p(s) && (typeof s.$ref == "string" ? r.set(s.$ref, (r.get(s.$ref) ?? 0) + 1) : v(s, o)), s);
    o(a), Object.values(t).forEach(o);
    const _ = new Set(Object.keys(t).filter((s) => r.get(`#/$defs/${s}`) === 1));
    if (!_.size) break;
    const u = (s) => p(s) ? typeof s.$ref == "string" && _.has(s.$ref.slice(8)) ? u(t[s.$ref.slice(8)]) : v(s, u) : s, h = u(a);
    if (!p(h)) throw new Error("synthesis_schema_projection_decision_shape_invalid");
    a = h, t = Object.fromEntries(Object.entries(t).filter(([s]) => !_.has(s)).map(([s, m]) => [s, u(m)]));
  }
  const c = new Map(Object.keys(t).sort().map((r, o) => [r, o.toString(16)])), l = (r) => {
    if (!p(r)) return r;
    const o = v(r, l);
    return typeof o.$ref == "string" && (o.$ref = `#/$defs/${c.get(o.$ref.slice(8))}`), o;
  }, f = l(a);
  if (!p(f)) throw new Error("synthesis_schema_projection_decision_shape_invalid");
  return { ...f, $defs: Object.fromEntries(Object.entries(t).map(([r, o]) => [c.get(r), l(o)])) };
}
function G(e) {
  const i = {}, t = (a, c = !0) => {
    if (!p(a)) return a;
    const l = v(a, (o) => t(o)), f = JSON.stringify(l);
    if (!c || f.length < 50) return l;
    const r = D("sha256").update(f).digest("hex").slice(0, 20);
    if (i[r] !== void 0 && JSON.stringify(i[r]) !== f) throw new Error("synthesis_schema_intern_collision");
    return i[r] = l, { $ref: `#/$defs/${r}` };
  }, n = t(F(e), !1);
  if (!p(n)) throw new Error("synthesis_schema_projection_decision_shape_invalid");
  return V(n, i);
}
function X(e) {
  const i = { ...e, properties: { ...e.properties, candidate: { oneOf: [e.properties.candidate, j.candidate] }, review_request: j.review_request } }, t = j.$defs.draft_event_probability_v1, n = { ...j.$defs, draft_event_probability_v1: { ...t, properties: {
    ...t.properties,
    outcome_given_occurrence: {
      ...t.properties.outcome_given_occurrence,
      description: "outcome_given_occurrence.conditioning_ref must equal event_probability.event_definition_ref, including kind=unavailable with probabilities=null. Use the same local_id or exact existing reference. Unknown probabilities stay null. This separate event-distribution identity does not condition ordinary market direction: keep market_event_conditioned=false when appropriate."
    }
  } } };
  return G({
    type: "object",
    oneOf: [i, j.prepare, j.finalize],
    description: "Use the frozen request protocol. decision_v2: prepare one authored draft, independent foreground reviewer, then finalize by the returned prepared_call_id. Event plans use perpetual-synthesis-draft.v2 with primary_draft, typed event_plan_draft and event_plan_originals; the server derives their exact plan/comparison refs from the authenticated source call before review. Use local:event: identities only for declared local bodies; server economic refs remain exact. Never put financial plans in assumption text, hash model content or copy a generated packet. Existing full candidate forms are archived compatibility only.",
    $defs: { ...e.$defs ?? {}, ...n }
  });
}
export {
  X as a,
  Y as d,
  g as p,
  W as s
};
//# sourceMappingURL=synthesis-tool-schema-CZ1FsJUV.js.map
