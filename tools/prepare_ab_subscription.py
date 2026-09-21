from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from anime_subscription_agent.subscription_service import (
    SubscriptionPreparationError,
    prepare_ab_subscription,
)


class PrepareAbSubscriptionTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        try:
            result = prepare_ab_subscription(
                self.runtime.credentials,
                rss_url=tool_parameters.get("rss_url"),
                expected_title=tool_parameters.get("expected_title"),
                expected_season=tool_parameters.get("expected_season"),
                expected_group=tool_parameters.get("expected_group"),
                expected_year=tool_parameters.get("expected_year"),
            )
        except SubscriptionPreparationError as exc:
            result = exc.to_result()
        yield self.create_json_message(result)
