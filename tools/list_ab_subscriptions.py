from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from anime_subscription_agent.dify_adapter import list_ab_subscriptions


class ListAbSubscriptionsTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        yield self.create_json_message(list_ab_subscriptions(self.runtime.credentials))
