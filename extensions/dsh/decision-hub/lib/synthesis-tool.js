import { w as s } from "./index-CWciLEwK.js";
import { defineTool as i } from "@deepseek-ai/dsh-tools";
const d = "decision-hub-synthesis-tool", h = ["tools"], n = "decision_hub_synthesis_submit", o = {
  candidate: {
    type: "json",
    required: !0,
    description: "The complete research-synthesis-candidate.v1 object. It must use the exact request_id and Evidence IDs from the canonical request and successful research results."
  }
};
function r() {
  return i({
    name: n,
    description: "Submit the final Decision Hub research synthesis. Call this exactly once after all approved evidence work is complete. Invalid schema is a Tool error that must be corrected inside this DSH session; this Tool does not fetch data, publish, notify, or trade.",
    parameters: o,
    output: {
      schema: { type: "json" },
      render: (e, t) => [{ type: "text", text: JSON.stringify(t) }]
    },
    async execute(e, t) {
      if (t.agent === void 0) throw new Error("decision_hub_synthesis_agent_required");
      return s.parse(e.candidate);
    }
  });
}
function u(e) {
  e.tools.register(r());
}
export {
  n as SYNTHESIS_TOOL_NAME,
  u as apply,
  r as createSynthesisToolDefinition,
  h as inject,
  d as name
};
//# sourceMappingURL=synthesis-tool.js.map
