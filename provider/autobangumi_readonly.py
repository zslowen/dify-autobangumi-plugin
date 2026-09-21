"""Dify provider validation for the read-only AutoBangumi connector."""

from typing import Any

from dify_plugin import ToolProvider
from dify_plugin.errors.tool import ToolProviderCredentialValidationError

from anime_subscription_agent.dify_adapter import get_ab_status


class AutoBangumiReadOnlyProvider(ToolProvider):
    def _validate_credentials(self, credentials: dict[str, Any]) -> None:
        try:
            get_ab_status(credentials)
        except Exception as exc:
            raise ToolProviderCredentialValidationError(
                "Could not authenticate to the configured AutoBangumi read-only service."
            ) from exc
