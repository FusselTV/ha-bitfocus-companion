"""Constants for the Bitfocus Companion integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "bitfocus_companion"

# Linked from the setup errors and the repair notices, because both of them ask the
# user to go and change something in Companion.
DOCS_URL: Final = "https://github.com/FusselTV/ha-bitfocus-companion/wiki"

# What the user leaves out is stored, not what they pick. A surface or connection
# added in Companion later then shows up without reopening the options.
CONF_EXCLUDED_SURFACES: Final = "excluded_surfaces"

# The Companion machine id, read from its mDNS record. Only discovered entries have
# one, because the REST API does not report it.
CONF_MACHINE_ID: Final = "machine_id"
CONF_EXCLUDED_CONNECTIONS: Final = "excluded_connections"

DEFAULT_PORT: Final = 8000

# Companion has no token management yet, only fixed strings compiled into the app.
# cpn_admin is the one that reaches both surfaces and connections, so it is the
# default until Companion ships tokens a user can create.
DEFAULT_TOKEN: Final = "cpn_admin"  # noqa: S105 - a published placeholder, not a secret
DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 5
MAX_SCAN_INTERVAL: Final = 3600

ISSUE_API_DISABLED: Final = "api_disabled"
ISSUE_CONNECTIONS_SCOPE_LOST: Final = "connections_scope_lost"
