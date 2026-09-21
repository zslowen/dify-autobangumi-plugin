from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from anime_subscription_agent.dify_adapter import list_ab_bangumi_rules


class ListAbBangumiRulesTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        yield self.create_json_message(
            list_ab_bangumi_rules(
                self.runtime.credentials,
                tool_parameters.get("title"),
                tool_parameters.get("season"),
                tool_parameters.get("limit"),
            )
        )
