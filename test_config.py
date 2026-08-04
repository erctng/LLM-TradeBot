from src.agents.agent_config import AgentConfig
from src.server.config_manager import ConfigManager
c = ConfigManager("/app")
agents = {"trend_agent_llm": True}
valid_keys = set(AgentConfig().get_enabled_agents().keys())
filtered = {k: bool(v) for k, v in agents.items() if k in valid_keys}
print("Filtered:", filtered)
current = c._get_agents_config()
merged = {**current, **filtered}
normalized = AgentConfig.from_dict({"agents": merged}).get_enabled_agents()
print("Normalized:", normalized)
c._update_agents_config(normalized)
with open("/app/config.yaml") as f:
    print("File content:", f.read())
