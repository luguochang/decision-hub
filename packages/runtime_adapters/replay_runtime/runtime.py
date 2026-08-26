from packages.runtime_adapters.fake_runtime.runtime import FakeAgentRuntime


class ReplayAgentRuntime(FakeAgentRuntime):
    runtime_id = "replay"
    runtime_version = "replay.v1"
