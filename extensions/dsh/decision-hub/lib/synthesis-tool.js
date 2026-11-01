import { createHash as b } from "node:crypto";
import { F as q, G as S } from "./baseline-transport-CMVYRXyy.js";
import { defineTool as I, parameterSchemaSpecToJsonSchema as C } from "@deepseek-ai/dsh-tools";
import { a4 as p, a5 as P, d as m, a6 as E, a7 as T, a8 as w, a9 as z, aa as A, ab as j, ac as N, P as k, ad as O, ae as F, af as H, ag as J, W as R, a3 as y, ah as D, ai as L } from "./provider-input-BzHtE8v4.js";
import { s as G, a as M, d as B } from "./synthesis-tool-schema-CZ1FsJUV.js";
const $ = "decision-hub-synthesis-tool", x = ["tools"], Y = "decision_hub_synthesis_submit", g = 1e6, W = {
  candidate: {
    ...G,
    required: !0,
    description: "The complete research-synthesis-candidate.v1 object. It must use the exact request_id and Evidence IDs from the canonical request and successful research results. Follow every nested field in this schema. horizons is a top-level sibling of causal_case. Every causal link requires both confirmation and invalidation. Put a mechanism explanation inside statement; do not replace the required fields with new keys. Lists of IDs must be unique. The canonical validator additionally enforces non-empty strings, probability bounds, list bounds and timezone-aware datetimes. Missing facts remain missing; do not invent evidence or calibrated probabilities."
  }
};
function U(n = {}) {
  const l = I({
    name: Y,
    description: "For decision_v2, prepare one complete authored draft and call the independent foreground reviewer. A genuine needs_revision/rejected result includes concrete findings: within the SAME original Task/gen/tool/child/deadline budgets, gather allowed evidence, revise the complete draft, prepare it again and obtain a NEW independent child review. Preserve every old attempt. Only finalize the latest approved prepared_call_id; old approvals never authorize changed packets. Product code computes references; never calculate SHA256 or copy nested packets. The following single-review instructions apply to legacy protocols. Freeze the complete final Decision Hub research synthesis after all approved evidence work is complete. A final-review-required error freezes this exact candidate; use the single budgeted reviewer, then resubmit unchanged. Invalid schema must be corrected inside this DSH session; this Tool does not fetch data, publish, notify, or trade.",
    // Preserve the original raw arguments for provider attestation and the
    // existing representation-only normalizer. The visible nested contract
    // guides new calls; strict Zod validation still checks the normalized value.
    parameters: { candidate: { type: "json" }, review_request: { type: "json" }, action: { type: "string" }, request_id: { type: "string" }, draft: { type: "json" }, prepared_call_id: { type: "string" } },
    output: {
      schema: { type: "json" },
      render: (i, e) => [{ type: "text", text: JSON.stringify(e) }]
    },
    finalizeContent(i, e) {
      if (!e.isError)
        try {
          const r = JSON.stringify(e.value);
          if (r === void 0 || Buffer.byteLength(r, "utf8") > g) return;
          const o = H.safeParse(e.value);
          if (o.success && o.data.preparation.prepared_call_id === i.callId) return [{ type: "text", text: r }];
          const s = J.safeParse(e.value);
          if (!s.success) return;
          const { candidate: h, review_gate: t } = s.data;
          if ((s.data.schema_version === "decision-hub-synthesis-tool-result.v3" || s.data.schema_version === "decision-hub-synthesis-tool-result.v4" || s.data.schema_version === "decision-hub-synthesis-tool-result.v5") && (s.data.review_gate.review_request_json !== JSON.stringify(s.data.review_request) || R(s.data.review_gate.review_request_json) !== s.data.review_gate.review_request_sha256)) return;
          if (s.data.schema_version === "decision-hub-synthesis-tool-result.v5") {
            const a = s.data.coverage_delivery;
            if (t.status !== "matched" || y(a.binding) !== y(t.binding) || a.finalize_call_id !== i.callId || a.root_cutoff.before_seq !== a.finalize_call_seq || a.source_snapshot.message_seq >= a.finalize_call_seq) return;
          }
          return t.binding.sessionId !== i.agent?.id || t.candidate_json !== JSON.stringify(h) || t.candidate_sha256 !== b("sha256").update(t.candidate_json, "utf8").digest("hex") || (t.status === "matched" ? t.reason !== "exact_target" || t.review_receipt_hash === null || t.review_call_id === null : t.reason === "exact_target" || t.review_receipt_hash !== null || t.review_call_id !== null) ? void 0 : [{ type: "text", text: r }];
        } catch {
          return;
        }
    },
    async execute(i, e) {
      if (e.agent === void 0) throw new Error("decision_hub_synthesis_agent_required");
      if (n.reviewContext === void 0) return q.parse(p(i.candidate));
      if (i.action !== void 0) {
        const t = i.action === "prepare" ? P.parse(i) : S.parse(i), a = await n.reviewContext(e.agent, t.request_id, e.signal, e.callId);
        if (m(a.request.output_schema_ref) !== "decision_v2") throw new Error("decision_hub_cross_protocol_preparation");
        if (n.providerInput && await n.providerInput(a, e.agent.id, e.callId, i, e.signal), e.signal.throwIfAborted(), t.action === "prepare") return E(a, i, e.callId);
        const c = T(a, t.prepared_call_id, e.callId), u = w(a, c.candidate, c.review_request, e.callId), v = z(a);
        if (v.evidence.length === 0) return { schema_version: "decision-hub-synthesis-tool-result.v4", ...c, review_gate: u };
        if (u.status !== "matched" || u.reason !== "exact_target") throw new Error("decision_hub_finalized_coverage_review_required");
        const _ = A(a, e.callId, v);
        if (_ === null) throw new Error("decision_hub_finalized_coverage_missing");
        const f = { schema_version: "decision-hub-synthesis-tool-result.v5", ...c, review_gate: u, coverage_delivery: _ };
        if (Buffer.byteLength(JSON.stringify(f), "utf8") > g)
          throw new Error("decision_hub_finalized_coverage_oversized");
        return f;
      }
      const r = i.candidate;
      if (typeof r != "object" || r === null || Array.isArray(r) || !("request_id" in r) || typeof r.request_id != "string" || !r.request_id) throw new Error("decision_hub_synthesis_request_required");
      const o = await n.reviewContext(e.agent, r.request_id, e.signal, e.callId);
      if (m(o.request.output_schema_ref) === "decision_v2") {
        const t = j(r), a = N(i.review_request, t);
        if (t.schema_version !== "research-decision-synthesis.v2" || a.schema_version !== "action-review-request.v2") throw new Error("decision_hub_perpetual_event_prepare_required");
        return n.providerInput && await n.providerInput(o, e.agent.id, e.callId, i, e.signal), e.signal.throwIfAborted(), {
          schema_version: "decision-hub-synthesis-tool-result.v3",
          candidate: t,
          review_request: a,
          review_gate: w(o, t, a, e.callId)
        };
      }
      if (i.review_request !== void 0) throw new Error("decision_hub_cross_protocol_review_packet");
      const s = k(o.request.output_schema_ref, r, p);
      e.signal.throwIfAborted(), n.providerInput && (await n.providerInput(o, e.agent.id, e.callId, i, e.signal), e.signal.throwIfAborted());
      const h = O(o, s, e.callId, p);
      return { schema_version: F(s) ? "decision-hub-synthesis-tool-result.v2" : "decision-hub-synthesis-tool-result.v1", candidate: s, review_gate: h };
    }
  }), d = C(W);
  return { ...l, parameters: n.reviewContext === void 0 ? { ...d } : { ...M(B(d)) } };
}
function ee(n) {
  n.tools.register(U({
    reviewContext: (l, d, i, e) => L(n.root, l, d, i, e),
    providerInput: (l, d, i, e, r) => D(n.root, l, d, i, e, r)
  }));
}
export {
  Y as SYNTHESIS_TOOL_NAME,
  ee as apply,
  U as createSynthesisToolDefinition,
  x as inject,
  $ as name,
  p as normalizeSynthesisCandidate
};
//# sourceMappingURL=synthesis-tool.js.map
