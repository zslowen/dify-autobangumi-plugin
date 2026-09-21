from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from anime_subscription_agent.dify_adapter import get_ab_rss_records


class GetAbRssRecordsTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        yield self.create_json_message(
            get_ab_rss_records(self.runtime.credentials, tool_parameters.get("rss_id"))
        )
