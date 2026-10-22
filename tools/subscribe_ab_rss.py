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
                preview_id=tool_parameters.get("preview_id"),
            )
        )
