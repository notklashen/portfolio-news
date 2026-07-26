"""Application-specific exceptions with safe, user-facing messages."""


class PortfolioNewsError(Exception):
    """Base class for expected application failures."""


class ConfigurationError(PortfolioNewsError):
    """Configuration is absent or invalid."""


class SheetError(PortfolioNewsError):
    """The configured spreadsheet could not be read or validated."""


class ResearchError(PortfolioNewsError):
    """The research response could not be obtained or validated."""


class TelegramError(PortfolioNewsError):
    """A Telegram message could not be delivered."""


class TelegramRenderError(PortfolioNewsError):
    """A digest cannot fit safely within Telegram's message limit."""


class AlreadyRunningError(PortfolioNewsError):
    """Another process currently owns the single-run lock."""

