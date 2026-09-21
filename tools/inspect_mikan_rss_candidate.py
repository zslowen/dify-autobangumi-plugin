from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from anime_subscription_agent.mikan_adapter import inspect_mikan_rss_candidate


class InspectMikanRssCandidateTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        yield self.create_json_message(
            inspect_mikan_rss_candidate(tool_parameters.get("rss_url"))
        )
