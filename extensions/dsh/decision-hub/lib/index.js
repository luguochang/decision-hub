import d from "@deepseek-ai/schemastery";
import { timingSafeEqual as D, createHash as I } from "node:crypto";
import { d as A, a as B, b as O, r as H, c as J, e as N, f as S, g as K, h as $, i as G, j as L, k as z, l as F, m as V, n as W, o as Z, Z as Y } from "./index-DHMPbtxF.js";
const Q = "x-decision-hub-host-key";
function X(i, e) {
  if (typeof i != "string" || e.length === 0) return !1;
  const t = Buffer.from(i), s = Buffer.from(e);
  return t.length === s.length && D(t, s);
}
class v extends Error {
  constructor(e) {
    super(e), this.code = e;
  }
}
async function C(i, e) {
  if (i.headers["content-type"]?.split(";", 1)[0]?.trim().toLowerCase() !== "application/json") throw new v("host_content_type_invalid");
  const s = [];
  let n = 0;
  for await (const r of i) {
    const a = Buffer.isBuffer(r) ? r : Buffer.from(r);
    if (n += a.length, n > e) throw new v("host_body_too_large");
    s.push(a);
  }
  try {
    const r = JSON.parse(Buffer.concat(s).toString("utf8"));
    if (typeof r != "object" || r === null || Array.isArray(r))
      throw new Error("JSON body must be an object");
    return r;
  } catch {
    throw new v("host_json_invalid");
  }
}
function m(i) {
  return (i instanceof Error ? i.message : String(i)).replace(/(authorization|api[-_]?key|token|secret|password)\s*[:=]\s*\S+/gi, "$1=[redacted]").slice(0, 1e3);
}
class u extends Error {
  constructor(e, t, s) {
    super(e, { cause: s }), this.code = e, this.retryable = t;
  }
}
class ee {
  constructor(e) {
    this.options = e, this.fetchImpl = e.fetchImpl ?? fetch;
  }
  fetchImpl;
  async readiness() {
    try {
      return (await this.request("/health/ready", { method: "GET" }, 1)).ok;
    } catch {
      return !1;
    }
  }
  async prompt(e, t) {
    const s = await this.request(`/v1/dsh/sessions/${encodeURIComponent(e)}/prompt?generation=${t}`, { method: "GET" });
    return A.parse(await this.json(s));
  }
  async link(e) {
    const t = await this.request(`/v1/dsh/sessions/${encodeURIComponent(e)}`, { method: "GET" });
    return B.parse(await this.json(t));
  }
  async linkBySession(e) {
    const t = await this.request(`/v1/dsh/sessions/by-session/${encodeURIComponent(e)}`, { method: "GET" });
    return B.parse(await this.json(t));
  }
  async businessStatus(e) {
    const t = await this.request(`/v1/dsh/sessions/${encodeURIComponent(e)}/business-status`, { method: "GET" });
    return O.parse(await this.json(t));
  }
  async researchDetail(e) {
    const t = await this.request(`/v1/research/runs/${encodeURIComponent(e)}`, { method: "GET" });
    return H.parse(await this.json(t));
  }
  async accepted(e) {
    await this.json(await this.request(`/v1/dsh/sessions/${encodeURIComponent(e.run_id)}/accepted`, {
      method: "PUT",
      body: JSON.stringify(e),
      headers: { "content-type": "application/json" }
    }));
  }
  async status(e) {
    await this.json(await this.request(`/v1/dsh/sessions/${encodeURIComponent(e.run_id)}/status`, {
      method: "PUT",
      body: JSON.stringify(e),
      headers: { "content-type": "application/json" }
    }));
  }
  async terminal(e) {
    await this.json(await this.request(`/v1/dsh/sessions/${encodeURIComponent(e.run_id)}/terminal`, {
      method: "PUT",
      body: JSON.stringify(e),
      headers: { "content-type": "application/json" }
    }));
  }
  async researchIntake(e, t) {
    const s = await this.request("/v1/research/observations", {
      method: "POST",
      body: JSON.stringify({
        text: e.text,
        source_id: e.source_id,
        source_type: "manual",
        language: e.language
      }),
      headers: {
        "content-type": "application/json",
        "idempotency-key": t
      }
    });
    return J.parse(await this.json(s));
  }
  async researchCommand(e, t) {
    const s = await this.request(`/v1/research/runs/${encodeURIComponent(e)}/commands`, {
      method: "POST",
      body: JSON.stringify(t),
      headers: {
        "content-type": "application/json",
        "idempotency-key": t.request_id,
        "x-owner-id": this.options.ownerId
      }
    });
    return N.parse(await this.json(s));
  }
  async request(e, t, s = this.options.attempts) {
    let n;
    for (let r = 1; r <= s; r += 1) {
      const a = new AbortController(), h = setTimeout(() => a.abort(), this.options.timeoutMs);
      try {
        const c = await this.fetchImpl(new URL(e, this.options.baseUrl), {
          ...t,
          signal: a.signal,
          headers: {
            "x-decision-hub-bridge-key": this.options.callbackKey,
            ...t.headers
          }
        });
        if (c.ok) return c;
        if (c.status < 500 || r === s)
          throw new u(`host_hub_http_${c.status}`, c.status >= 500);
        n = new Error(`Hub returned ${c.status}`);
      } catch (c) {
        if (c instanceof u && !c.retryable) throw c;
        if (n = c, r === s) break;
      } finally {
        clearTimeout(h);
      }
    }
    throw new u("host_callback_unreachable", !0, m(n));
  }
  async json(e) {
    try {
      return await e.json();
    } catch (t) {
      throw new u("host_hub_response_invalid", !1, t);
    }
  }
}
const te = 15e4;
function j(i, e, t = 1) {
  if (!Number.isInteger(t) || t < 1) throw new Error("host_generation_invalid");
  const s = I("sha256").update(`dsh-host-bridge.v1\0${i}\0${e}`).digest("hex"), n = t === 1 ? s : I("sha256").update(`dsh-host-turn.v1\0${i}\0${e}\0${t}`).digest("hex");
  return { sessionId: `dsh_${s}`, requestId: `req_${n}` };
}
class se {
  byRun = /* @__PURE__ */ new Map();
  runBySession = /* @__PURE__ */ new Map();
  admit(e) {
    const t = j(e.run_id, e.request_hash, e.generation);
    if (t.sessionId !== e.deterministic_session_id || t.requestId !== e.deterministic_request_id)
      throw new Error("host_deterministic_id_mismatch");
    const s = this.byRun.get(e.run_id);
    if (s !== void 0) {
      if (s.requestHash !== e.request_hash || s.sessionId !== e.deterministic_session_id)
        throw new Error("host_session_conflict");
      if (s.generation === e.generation) {
        if (s.requestId !== e.deterministic_request_id) throw new Error("host_session_conflict");
        return { correlation: s, duplicate: !0 };
      }
      if (e.generation !== s.generation + 1 || s.state !== "completed")
        throw new Error("host_generation_conflict");
      return s.requestId = e.deterministic_request_id, s.generation = e.generation, s.modelStepTimeoutMs = e.model_step_timeout_ms, s.modelStepTimeoutTriggered = !1, s.state = "admitted", s.errorCode = null, s.acceptedAt = null, s.terminalAt = null, { correlation: s, duplicate: !1 };
    }
    const n = this.runBySession.get(e.deterministic_session_id);
    if (n !== void 0 && n !== e.run_id) throw new Error("host_session_conflict");
    const r = {
      runId: e.run_id,
      requestHash: e.request_hash,
      sessionId: e.deterministic_session_id,
      requestId: e.deterministic_request_id,
      generation: e.generation,
      modelStepTimeoutMs: e.model_step_timeout_ms,
      modelStepTimeoutTriggered: !1,
      state: "admitted",
      lastSeq: 0,
      errorCode: null,
      acceptedAt: null,
      terminalAt: null
    };
    return this.byRun.set(r.runId, r), this.runBySession.set(r.sessionId, r.runId), { correlation: r, duplicate: !1 };
  }
  recover(e) {
    const t = j(e.run_id, e.request_hash, e.generation);
    if (t.sessionId !== e.dsh_session_id) throw new Error("host_session_conflict");
    const s = {
      runId: e.run_id,
      requestHash: e.request_hash,
      sessionId: e.dsh_session_id,
      requestId: t.requestId,
      generation: e.generation,
      // A watchdog reason is persisted in the link's error_code, allowing a
      // restarted Host to retain the same terminal semantics.
      // The submit payload is not part of the durable link view yet. Keep a
      // bounded recovery default until the next generation re-submits its
      // canonical budget instead of disabling the watchdog after restart.
      modelStepTimeoutMs: te,
      modelStepTimeoutTriggered: e.error_code === "dsh_model_step_timeout",
      state: e.state,
      lastSeq: e.last_seq,
      errorCode: e.error_code,
      acceptedAt: e.accepted_at,
      terminalAt: e.terminal_at
    };
    return this.byRun.set(s.runId, s), this.runBySession.set(s.sessionId, s.runId), s;
  }
  getRun(e) {
    return this.byRun.get(e);
  }
  getSession(e) {
    const t = this.runBySession.get(e);
    return t === void 0 ? void 0 : this.byRun.get(t);
  }
}
function U(i) {
  return new Date(i ?? Date.now()).toISOString();
}
function P(i, e) {
  return i.findIndex((t) => {
    if (t.type !== "user/message") return !1;
    const s = t.data.source;
    return typeof s == "object" && s !== null && s.kind === "user" && s.rpcId === e;
  });
}
function ne(i) {
  const e = i.data.message;
  if (typeof e != "object" || e === null) return "";
  const t = e.content;
  return Array.isArray(t) ? t.flatMap((s) => {
    if (typeof s != "object" || s === null) return [];
    const n = s;
    return n.type === "text" && typeof n.text == "string" ? [n.text] : [];
  }).join(`
`).trim() : "";
}
function re(i, e) {
  return P(i.events, e) >= 0;
}
function ie(i, e) {
  const t = e.events, s = t.reduce((l, p) => Math.max(l, p.seq), 0), n = P(t, i.requestId);
  let r = i.state, a = i.errorCode, h = null, c = null;
  if (n >= 0) {
    const l = t.slice(n + 1), p = l.find((f) => f.type === "turn/end");
    if (p === void 0)
      r = r === "admitted" ? "running" : r;
    else {
      const f = p.data.reason, _ = typeof f == "object" && f !== null ? String(f.kind ?? "unknown") : "unknown", k = l.filter((T) => T.type === "assistant/message").at(-1), x = k === void 0 ? "" : ne(k), w = U(p.time), g = `dsh://sessions/${encodeURIComponent(i.sessionId)}?last_seq=${s}`;
      if (i.modelStepTimeoutTriggered)
        r = "failed", a = "dsh_model_step_timeout", h = S.parse({
          schema_version: "dsh-session-completion.v1",
          run_id: i.runId,
          dsh_session_id: i.sessionId,
          terminal_status: "failed",
          generation: i.generation,
          last_seq: s,
          trace_ref: g,
          result_ref: null,
          result_hash: null,
          completed_at: w,
          error: {
            code: "dsh_model_step_timeout",
            message: "DSH model step exceeded the Host watchdog budget",
            retryable: !0
          }
        });
      else if ((_ === "completed" || _ === "max-tokens") && k !== void 0) {
        const T = l.filter((b) => b.type !== "assistant/chunk" && b.type !== "request/header").map((b) => ({
          method: "session.event",
          payload: { sessionId: i.sessionId, event: b }
        })), q = JSON.stringify(T), M = I("sha256").update(x).update("\0").update(q).digest("hex");
        c = K.parse({
          schema_version: "dsh-session-result.v1",
          run_id: i.runId,
          dsh_session_id: i.sessionId,
          generation: i.generation,
          last_seq: s,
          final_response: x,
          finish_reason: _,
          events_json: q,
          started_at: U(t[n]?.time ?? e.meta.createdAt),
          finished_at: w,
          trace_ref: g,
          result_hash: M
        }), r = "completed", h = S.parse({
          schema_version: "dsh-session-completion.v1",
          run_id: i.runId,
          dsh_session_id: i.sessionId,
          terminal_status: "completed",
          generation: i.generation,
          last_seq: s,
          trace_ref: g,
          result_ref: `dsh-host://runs/${encodeURIComponent(i.runId)}/result`,
          result_hash: M,
          completed_at: w,
          error: null
        });
      } else _ === "aborted" ? (r = "cancelled", h = S.parse({
        schema_version: "dsh-session-completion.v1",
        run_id: i.runId,
        dsh_session_id: i.sessionId,
        terminal_status: "cancelled",
        generation: i.generation,
        last_seq: s,
        trace_ref: g,
        result_ref: null,
        result_hash: null,
        completed_at: w,
        error: null
      })) : (r = "failed", a = _ === "completed" ? "host_terminal_result_unavailable" : `dsh_turn_${_}`, h = S.parse({
        schema_version: "dsh-session-completion.v1",
        run_id: i.runId,
        dsh_session_id: i.sessionId,
        terminal_status: "failed",
        generation: i.generation,
        last_seq: s,
        trace_ref: g,
        result_ref: null,
        result_hash: null,
        completed_at: w,
        error: { code: a, message: "DSH turn did not produce an attested result", retryable: !1 }
      }));
    }
  }
  return { status: $.parse({
    schema_version: "dsh-session-status.v1",
    run_id: i.runId,
    dsh_session_id: i.sessionId,
    state: r,
    generation: i.generation,
    last_seq: s,
    observed_at: (/* @__PURE__ */ new Date()).toISOString(),
    error_code: a
  }), completion: h, result: c };
}
const R = "4d172b7d416c332d2607a461d86487fdac0a62a5ddb1e3593ea4bfff8bc3960e", oe = "0a53fb55bea101816fa226bb964ae2bed71c343b", E = "0.1.2-alpha.2", ae = /^[a-f0-9]{64}$/, ce = /^[A-Za-z0-9._:-]{8,256}$/;
function he(i) {
  return {
    hubBaseUrl: i.hubBaseUrl ?? "http://127.0.0.1:8000",
    decisionDeskBaseUrl: i.decisionDeskBaseUrl ?? "http://127.0.0.1:8000",
    inboundKey: i.inboundKey ?? "",
    callbackKey: i.callbackKey ?? "",
    defaultWorkspaceCwd: i.defaultWorkspaceCwd ?? process.cwd(),
    allowedPermissionRefs: new Set(i.allowedPermissionRefs ?? ["decision-hub://permissions/research-only"]),
    clientPlugin: i.clientPlugin ?? !1,
    requireClientPlugin: i.requireClientPlugin ?? !1,
    maxBodyBytes: i.maxBodyBytes ?? 12e5,
    operationTimeoutMs: i.operationTimeoutMs ?? 3e4,
    callbackTimeoutMs: i.callbackTimeoutMs ?? 5e3,
    callbackAttempts: i.callbackAttempts ?? 3,
    ownerId: i.ownerId ?? "dsh-web-owner",
    runtimeMode: i.runtimeMode ?? "live",
    sourceCommit: i.sourceCommit ?? "",
    sourceVersion: i.sourceVersion ?? "",
    pluginBuildHash: i.pluginBuildHash ?? "",
    ...i.fetchImpl === void 0 ? {} : { fetchImpl: i.fetchImpl }
  };
}
class o extends Error {
  constructor(e, t, s = !1) {
    super(e), this.code = e, this.status = t, this.retryable = s;
  }
}
class de {
  constructor(e, t) {
    this.ctx = e, this.config = t, this.hub = new ee({
      baseUrl: t.hubBaseUrl,
      callbackKey: t.callbackKey,
      timeoutMs: t.callbackTimeoutMs,
      attempts: t.callbackAttempts,
      ownerId: t.ownerId,
      ...t.fetchImpl === void 0 ? {} : { fetchImpl: t.fetchImpl }
    });
  }
  correlations = new se();
  hub;
  results = /* @__PURE__ */ new Map();
  terminalSent = /* @__PURE__ */ new Set();
  terminalInFlight = /* @__PURE__ */ new Map();
  modelStepTimers = /* @__PURE__ */ new Map();
  hubReachable = !1;
  start() {
    const e = [
      this.ctx.webServer.register({ kind: "exact", path: "/decision-hub/v1/readiness", handler: (t, s) => this.readiness(t, s) }),
      this.ctx.webServer.register({ kind: "prefix", path: "/decision-hub/v1/runs", handler: (t, s) => this.runs(t, s) }),
      this.ctx.webServer.register({ kind: "exact", path: "/api/decision-hub/research", handler: (t, s) => this.researchIntake(t, s) }),
      this.ctx.webServer.register({ kind: "exact", path: "/api/decision-hub/research/retry", handler: (t, s) => this.researchRetry(t, s) }),
      this.ctx.connection.fetch.register({
        path: "/api/decision-hub/status",
        methods: ["GET", "HEAD"],
        fetch: (t) => this.browserStatus(t)
      }),
      this.ctx.connection.fetch.register({
        path: "/api/decision-hub/report",
        methods: ["GET", "HEAD"],
        fetch: (t) => this.browserReport(t)
      }),
      this.ctx.on("api-session/status", (t, s) => {
        this.onStatus(t, s);
      }),
      this.ctx.on("api-session/error", (t, s) => {
        this.onError(t, s);
      }),
      this.ctx.on("session/event", (t, s) => {
        this.onSessionEvent(t.id, s);
      })
    ];
    return this.hub.readiness().then((t) => {
      this.hubReachable = t;
    }), () => {
      for (const t of this.modelStepTimers.values()) clearTimeout(t);
      this.modelStepTimers.clear();
      for (const t of e.reverse()) t();
    };
  }
  identity() {
    return {
      source_commit: this.config.sourceCommit,
      source_version: this.config.sourceVersion,
      package_versions: {
        "@deepseek-ai/dsh-api-session-controller": E,
        "@deepseek-ai/dsh-host-webserver": E
      },
      plugin_build_hash: R
    };
  }
  async readiness(e, t) {
    if (e.method !== "GET") return this.error(t, new o("host_method_not_allowed", 405));
    this.hubReachable = await this.hub.readiness();
    const s = this.versionCompatible(), n = s && this.hubReachable && (!this.config.requireClientPlugin || this.config.clientPlugin) && this.config.inboundKey.length > 0 && this.config.callbackKey.length > 0, r = G.parse({
      schema_version: "dsh-host-readiness.v1",
      ready: n,
      version_compatible: s,
      session_controller: !0,
      client_plugin: this.config.clientPlugin,
      hub_reachable: this.hubReachable,
      upstream_identity: this.identity(),
      checked_at: (/* @__PURE__ */ new Date()).toISOString(),
      error_code: n ? null : this.readinessError()
    });
    this.json(t, n ? 200 : 503, r);
  }
  readinessError() {
    return this.config.inboundKey.length === 0 || this.config.callbackKey.length === 0 ? "host_secret_missing" : this.versionCompatible() ? this.hubReachable ? this.config.requireClientPlugin && !this.config.clientPlugin ? "host_client_plugin_missing" : "host_not_ready" : "host_hub_unreachable" : "host_version_incompatible";
  }
  versionCompatible() {
    return this.config.sourceCommit === oe && this.config.sourceVersion === E && this.config.pluginBuildHash === R && ae.test(R);
  }
  async browserStatus(e) {
    this.hubReachable = await this.hub.readiness();
    const t = new URL(e.url), s = t.searchParams.get("session_id"), n = t.searchParams.get("run_id");
    let r = null, a = null;
    if (s !== null && s.length > 0)
      try {
        r = await this.hub.linkBySession(s);
      } catch (c) {
        if (!(c instanceof u && c.code === "host_hub_http_404"))
          return this.browserJson(503, {
            schema_version: "dsh-browser-status.v1",
            ready: !1,
            run_id: null,
            runtime_mode: this.config.runtimeMode,
            interaction_mode: this.config.runtimeMode === "replay" ? "read_only" : "interactive",
            dsh_session_id: s,
            state: null,
            error_code: "host_hub_unreachable",
            decision_desk_url: this.decisionDeskUrl(null),
            business: null
          }, e.method);
      }
    else if (n !== null && n.length > 0)
      try {
        r = await this.hub.link(n);
      } catch (c) {
        if (!(c instanceof u && c.code === "host_hub_http_404"))
          return this.browserJson(503, {
            schema_version: "dsh-browser-status.v1",
            ready: !1,
            run_id: n,
            runtime_mode: this.config.runtimeMode,
            interaction_mode: this.config.runtimeMode === "replay" ? "read_only" : "interactive",
            dsh_session_id: null,
            state: null,
            error_code: "host_hub_unreachable",
            decision_desk_url: this.decisionDeskUrl(n),
            business: null
          }, e.method);
      }
    if (r?.run_id !== void 0)
      try {
        a = await this.businessStatus(r.run_id);
      } catch (c) {
        if (!(c instanceof u && c.code === "host_hub_http_404"))
          return this.browserJson(503, {
            schema_version: "dsh-browser-status.v1",
            ready: !1,
            runtime_mode: this.config.runtimeMode,
            interaction_mode: this.config.runtimeMode === "replay" ? "read_only" : "interactive",
            run_id: r.run_id,
            dsh_session_id: r.dsh_session_id,
            state: r.state,
            error_code: "host_business_summary_unavailable",
            decision_desk_url: this.decisionDeskUrl(r.run_id),
            business: null
          }, e.method);
      }
    const h = this.versionCompatible() && this.hubReachable;
    return this.browserJson(h ? 200 : 503, {
      schema_version: "dsh-browser-status.v1",
      ready: h,
      runtime_mode: this.config.runtimeMode,
      interaction_mode: this.config.runtimeMode === "replay" ? "read_only" : "interactive",
      run_id: r?.run_id ?? null,
      dsh_session_id: r?.dsh_session_id ?? s,
      state: r?.state ?? null,
      error_code: h ? r?.error_code ?? null : this.readinessError(),
      decision_desk_url: this.decisionDeskUrl(r?.run_id ?? null),
      business: a
    }, e.method);
  }
  async businessStatus(e) {
    return this.hub.businessStatus(e);
  }
  async browserReport(e) {
    const t = new URL(e.url).searchParams.get("run_id");
    if (t === null || t.length === 0)
      return this.rawBrowserJson(400, {
        error: { code: "host_run_id_required", retryable: !1 }
      }, e.method);
    try {
      const s = await this.hub.researchDetail(t);
      return this.rawBrowserJson(200, s, e.method);
    } catch (s) {
      const n = s instanceof u && s.code === "host_hub_http_404";
      return this.rawBrowserJson(n ? 404 : 503, {
        error: {
          code: n ? "host_research_report_not_found" : "host_research_report_unavailable",
          retryable: !n
        }
      }, e.method);
    }
  }
  decisionDeskUrl(e) {
    const t = new URL(this.config.decisionDeskBaseUrl);
    return e !== null && t.searchParams.set("run_id", e), t.toString();
  }
  browserJson(e, t, s) {
    const n = L.parse(t);
    return this.rawBrowserJson(e, n, s);
  }
  rawBrowserJson(e, t, s) {
    return new Response(s === "HEAD" ? null : JSON.stringify(t), {
      status: e,
      headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" }
    });
  }
  async runs(e, t) {
    try {
      if (this.authorize(e), !this.versionCompatible()) throw new o("host_version_incompatible", 503);
      const s = new URL(e.url ?? "/", "http://localhost"), n = /^\/decision-hub\/v1\/runs\/([^/]+)(?:\/(cancel|result))?$/.exec(s.pathname);
      if (n === null) throw new o("host_route_not_found", 404);
      const r = decodeURIComponent(n[1]), a = n[2];
      if (a === "cancel") {
        if (e.method !== "POST") throw new o("host_method_not_allowed", 405);
        return await this.cancel(r, t);
      }
      if (a === "result") {
        if (e.method !== "GET") throw new o("host_method_not_allowed", 405);
        return await this.result(r, t);
      }
      if (e.method === "PUT") return await this.submit(r, e, t);
      if (e.method === "GET") return await this.status(r, t);
      throw new o("host_method_not_allowed", 405);
    } catch (s) {
      this.error(t, this.normalizeError(s));
    }
  }
  async researchIntake(e, t) {
    try {
      if (e.method !== "POST") throw new o("host_method_not_allowed", 405);
      if (this.config.runtimeMode === "replay") throw new o("host_replay_read_only", 409);
      if (!this.versionCompatible()) throw new o("host_version_incompatible", 503);
      const s = z.parse(await C(e, this.config.maxBodyBytes)), n = e.headers["idempotency-key"];
      if (typeof n != "string" || !ce.test(n))
        throw new o("host_intake_idempotency_required", 400);
      const r = `dsh-intake.v2\0${n}`, a = `dsh-intake-${I("sha256").update(r).digest("hex")}`, h = await this.hub.researchIntake(s, a), c = F.parse({
        schema_version: "dsh-research-intake-accepted.v1",
        event_id: h.event_id,
        run_id: h.run_id,
        status: h.status,
        status_url: h.status_url,
        decision_desk_url: this.decisionDeskUrl(h.run_id)
      });
      this.json(t, 202, c);
    } catch (s) {
      this.error(t, this.normalizeError(s));
    }
  }
  async researchRetry(e, t) {
    try {
      if (e.method !== "POST") throw new o("host_method_not_allowed", 405);
      if (this.config.runtimeMode === "replay") throw new o("host_replay_read_only", 409);
      if (!this.versionCompatible()) throw new o("host_version_incompatible", 503);
      const s = V.parse(
        await C(e, this.config.maxBodyBytes)
      );
      if (s.command !== "retry") throw new o("host_command_unsupported", 422);
      const n = "dsh-retry:";
      if (!s.request_id.startsWith(n) || s.request_id.length <= n.length)
        throw new o("host_retry_request_id_invalid", 422);
      const r = s.request_id.slice(n.length), a = await this.hub.researchCommand(r, s);
      this.json(t, 200, a);
    } catch (s) {
      this.error(t, this.normalizeError(s));
    }
  }
  authorize(e) {
    if (!X(e.headers[Q], this.config.inboundKey))
      throw new o("host_unauthorized", 401);
  }
  async submit(e, t, s) {
    const n = W.parse(await C(t, this.config.maxBodyBytes));
    if (n.run_id !== e) throw new o("host_run_id_mismatch", 400);
    if (Date.parse(n.deadline_at) <= Date.now()) throw new o("host_deadline_elapsed", 408);
    this.validateRefs(n);
    const { correlation: r } = this.correlations.admit(n), a = this.deadlineSignal(n.deadline_at);
    let h = null;
    try {
      h = await this.ctx.sessionController.inspect(r.sessionId, a);
    } catch {
    }
    const c = await this.resolveWorkspace();
    if ((h === null || !c.sessionIds.includes(r.sessionId)) && (await this.ctx.sessionController.create({
      sessionId: r.sessionId,
      workspaceId: c.id,
      ...n.agent_preset === null ? {} : { agentPreset: n.agent_preset }
    })).sessionId !== r.sessionId)
      throw new o("host_session_identity_changed", 502);
    const y = Z.parse({
      schema_version: "dsh-session-accepted.v1",
      run_id: e,
      dsh_session_id: r.sessionId,
      accepted_at: (/* @__PURE__ */ new Date()).toISOString(),
      generation: r.generation
    });
    if (await this.hub.accepted(y), r.acceptedAt = y.accepted_at, h === null || !re(h, r.requestId)) {
      const l = await this.hub.prompt(e, r.generation);
      if (l.run_id !== e || l.dsh_session_id !== r.sessionId || l.request_id !== r.requestId || l.request_hash !== r.requestHash || l.generation !== r.generation)
        throw new o("host_prompt_conflict", 409);
      await this.ctx.sessionController.prompt({
        requestId: r.requestId,
        sessionId: r.sessionId,
        mode: "queue",
        content: [{ type: "text", text: l.prompt }]
      }, a);
    }
    this.json(s, 202, y);
  }
  validateRefs(e) {
    if (e.workspace_ref !== "decision-hub://workspace/default")
      throw new o("host_workspace_ref_unsupported", 422);
    if (e.prompt_ref !== `hub://runs/${e.run_id}/prompts/${e.generation}`)
      throw new o("host_prompt_ref_unsupported", 422);
    if (!this.config.allowedPermissionRefs.has(e.permission_ref))
      throw new o("host_permission_ref_unsupported", 422);
  }
  async resolveWorkspace() {
    let e;
    try {
      e = await this.ctx.workspaceRegistry.resolveByPath(this.config.defaultWorkspaceCwd);
    } catch {
      throw new o("host_workspace_resolution_failed", 503, !0);
    }
    if (e === void 0)
      throw new o("host_workspace_not_registered", 503);
    return e;
  }
  async status(e, t) {
    const s = await this.correlation(e), n = await this.inspect(s);
    await this.maybeTerminal(s, n.completion, n.result), this.json(t, 200, n.status);
  }
  async result(e, t) {
    const s = await this.correlation(e), n = await this.inspect(s);
    if (n.result === null) throw new o("host_terminal_result_unavailable", 409);
    await this.maybeTerminal(s, n.completion, n.result), this.json(t, 200, n.result);
  }
  async cancel(e, t) {
    const s = await this.correlation(e);
    this.clearModelStepTimer(s.sessionId), ["completed", "failed", "cancelled"].includes(s.state) || await this.ctx.sessionController.cancel({ sessionId: s.sessionId });
    const n = await this.inspect(s);
    await this.maybeTerminal(s, n.completion, n.result), this.json(t, 202, n.status);
  }
  async correlation(e) {
    const t = this.correlations.getRun(e);
    if (t !== void 0) return t;
    try {
      return this.correlations.recover(await this.hub.link(e));
    } catch (s) {
      throw s instanceof u && s.code === "host_hub_http_404" ? new o("host_run_not_found", 404) : s;
    }
  }
  async inspect(e) {
    const t = new AbortController(), s = setTimeout(() => t.abort(), this.config.operationTimeoutMs);
    try {
      const n = ie(e, await this.ctx.sessionController.inspect(e.sessionId, t.signal));
      return e.state = n.status.state, e.lastSeq = n.status.last_seq, e.errorCode = n.status.error_code, ["completed", "failed", "cancelled"].includes(n.status.state) && this.clearModelStepTimer(e.sessionId), n;
    } finally {
      clearTimeout(s);
    }
  }
  async reconcileSession(e) {
    const t = this.correlations.getSession(e);
    if (t !== void 0)
      try {
        const s = await this.inspect(t);
        await this.maybeTerminal(t, s.completion, s.result);
      } catch (s) {
        this.ctx.logger.warn(`decision-hub terminal reconciliation failed: ${m(s)}`);
      }
  }
  /**
   * Enforce the canonical per-model-step budget using only public DSH seams.
   * The timer is Host-owned; DSH remains the owner of the Agent Loop and
   * emits the eventual terminal event after the cancel request is accepted.
   */
  onSessionEvent(e, t) {
    const s = this.correlations.getSession(e);
    if (s !== void 0) {
      if (t.type === "step/start") {
        if (this.clearModelStepTimer(e), s.modelStepTimeoutTriggered) return;
        const n = s.modelStepTimeoutMs;
        if (!Number.isFinite(n) || n <= 0) return;
        const r = setTimeout(() => {
          this.modelStepTimers.delete(e), s.modelStepTimeoutTriggered = !0, s.errorCode = "dsh_model_step_timeout";
          try {
            Promise.resolve(this.ctx.sessionController.cancel({ sessionId: e })).catch((a) => this.ctx.logger.warn(`decision-hub model-step watchdog cancel failed: ${m(a)}`));
          } catch (a) {
            this.ctx.logger.warn(`decision-hub model-step watchdog cancel failed: ${m(a)}`);
          }
        }, n);
        this.modelStepTimers.set(e, r);
        return;
      }
      (t.type === "step/end" || t.type === "turn/end") && (this.clearModelStepTimer(e), t.type === "turn/end" && this.reconcileSession(e));
    }
  }
  clearModelStepTimer(e) {
    const t = this.modelStepTimers.get(e);
    t !== void 0 && (clearTimeout(t), this.modelStepTimers.delete(e));
  }
  async maybeTerminal(e, t, s) {
    if (t === null) return;
    s !== null && this.results.set(e.runId, s);
    const n = `${e.runId}:${t.generation}:${t.terminal_status}`;
    if (this.terminalSent.has(n)) return;
    const r = this.terminalInFlight.get(n);
    if (r !== void 0) return await r;
    const a = this.hub.terminal(t).then(() => {
      this.terminalSent.add(n), e.terminalAt = t.completed_at;
    });
    this.terminalInFlight.set(n, a);
    try {
      await a;
    } finally {
      this.terminalInFlight.get(n) === a && this.terminalInFlight.delete(n);
    }
  }
  async onStatus(e, t) {
    const s = this.correlations.getSession(e);
    if (s === void 0) return;
    t || this.clearModelStepTimer(e), s.state = t ? "running" : "idle";
    const n = $.parse({
      schema_version: "dsh-session-status.v1",
      run_id: s.runId,
      dsh_session_id: s.sessionId,
      state: s.state,
      generation: s.generation,
      last_seq: s.lastSeq,
      observed_at: (/* @__PURE__ */ new Date()).toISOString(),
      error_code: s.errorCode
    });
    try {
      await this.hub.status(n);
    } catch (r) {
      this.ctx.logger.warn(`decision-hub status callback failed: ${m(r)}`);
    }
    t || await this.reconcileSession(e);
  }
  onError(e, t) {
    const s = this.correlations.getSession(e);
    s !== void 0 && (s.state = "unknown", s.errorCode = "dsh_session_error", this.reconcileSession(e));
  }
  deadlineSignal(e) {
    const t = Math.min(this.config.operationTimeoutMs, Date.parse(e) - Date.now());
    if (t <= 0) throw new o("host_deadline_elapsed", 408);
    return AbortSignal.timeout(t);
  }
  normalizeError(e) {
    if (e instanceof o) return e;
    if (e instanceof v) {
      const t = e.code === "host_body_too_large" ? 413 : e.code === "host_content_type_invalid" ? 415 : 400;
      return new o(e.code, t);
    }
    return e instanceof Y ? new o("host_contract_invalid", 422) : e instanceof u ? new o(e.code, e.retryable ? 503 : 502, e.retryable) : e instanceof Error && e.message.startsWith("host_") ? new o(e.message, 409) : e instanceof DOMException && e.name === "TimeoutError" ? new o("host_deadline_elapsed", 408, !0) : (this.ctx.logger.warn(`decision-hub route failed: ${m(e)}`), new o("host_internal_error", 500));
  }
  error(e, t) {
    this.json(e, t.status, { error: { code: t.code, message: t.code, retryable: t.retryable } });
  }
  json(e, t, s) {
    e.headersSent || (e.writeHead(t, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" }), e.end(JSON.stringify(s)));
  }
}
const me = "decision-hub", pe = ["webServer", "sessionController", "workspaceRegistry", "connection"], fe = d.object({
  hubBaseUrl: d.string().default("http://127.0.0.1:8000"),
  decisionDeskBaseUrl: d.string().default("http://127.0.0.1:8000"),
  inboundKey: d.string(),
  callbackKey: d.string(),
  defaultWorkspaceCwd: d.string(),
  allowedPermissionRefs: d.array(d.string()).default(["decision-hub://permissions/research-only"]),
  clientPlugin: d.boolean().default(!1),
  requireClientPlugin: d.boolean().default(!1),
  maxBodyBytes: d.natural().min(1).default(12e5),
  operationTimeoutMs: d.natural().min(1).default(3e4),
  callbackTimeoutMs: d.natural().min(1).default(5e3),
  callbackAttempts: d.natural().min(1).max(5).default(3),
  ownerId: d.string().default("dsh-web-owner"),
  runtimeMode: d.union(["live", "replay"]).default("live"),
  sourceCommit: d.string(),
  sourceVersion: d.string(),
  pluginBuildHash: d.string()
});
function we(i, e) {
  const t = new de(i, he(e));
  i.effect(() => t.start(), "decision-hub.host-bridge");
}
export {
  fe as Config,
  de as DecisionHubHostBridge,
  we as apply,
  pe as inject,
  me as name,
  he as resolveHostConfig
};
//# sourceMappingURL=index.js.map
