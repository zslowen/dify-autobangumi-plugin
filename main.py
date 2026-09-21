"""Dify plugin entrypoint."""

import sys
from pathlib import Path

# The existing connector is a normal src-layout Python package. Dify loads this
# entrypoint from the plugin root, so make that package available before it loads
# provider and tool source files.
sys.path.insert(0, str(Path(__file__).parent / "src"))

from dify_plugin import DifyPluginEnv, Plugin


plugin = Plugin(DifyPluginEnv())


if __name__ == "__main__":
    plugin.run()
