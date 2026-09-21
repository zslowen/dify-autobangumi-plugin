from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from anime_subscription_agent.subscription_service import subscribe_ab_rss


class SubscribeAbRssTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        yield self.create_json_message(
            subscribe_ab_rss(
                self.runtime.credentials,
                rss_url=tool_parameters.get("rss_url"),
                expected_title=tool_parameters.get("expected_title"),
                expected_season=tool_parameters.get("expected_season"),
                expected_group=tool_parameters.get("expected_group"),
                preview_id=tool_parameters.get("preview_id"),
                expected_year=tool_parameters.get("expected_year"),
            )
        )
