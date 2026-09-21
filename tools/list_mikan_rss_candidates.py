from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from anime_subscription_agent.mikan_adapter import list_mikan_rss_candidates


class ListMikanRssCandidatesTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        yield self.create_json_message(
            list_mikan_rss_candidates(
                tool_parameters.get("bangumi_id"),
                tool_parameters.get("fansub_group"),
                tool_parameters.get("subtitle_language"),
                tool_parameters.get("resolution"),
                tool_parameters.get("limit"),
            )
        )
