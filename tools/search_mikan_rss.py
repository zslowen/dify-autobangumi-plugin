from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from anime_subscription_agent.mikan_adapter import search_mikan_rss


class SearchMikanRssTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        yield self.create_json_message(
            search_mikan_rss(
                tool_parameters.get("anime_title"),
                tool_parameters.get("fansub_group"),
            )
        )
