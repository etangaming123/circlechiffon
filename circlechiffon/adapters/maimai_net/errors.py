class MaimaiNetError(Exception):
    """Base class for anything that goes wrong talking to maimai DX NET."""


class InvalidCredentials(MaimaiNetError):
    """SEGA ID username/password (or the stored session cookie) was rejected."""


class TotpRequired(MaimaiNetError):
    """The gateway wants a TOTP code but none (or an invalid one) was supplied."""


class AimeCardUnavailable(MaimaiNetError):
    """Login to the SEGA ID succeeded, but no usable Aime card / game profile
    is registered on maimai DX NET for this account."""


class MaintenanceError(MaimaiNetError):
    """maimai DX NET is currently under maintenance."""


class SessionExpired(MaimaiNetError):
    """The stored session cookie is no longer valid; the user must /cc-login again."""


class UnexpectedResponse(MaimaiNetError):
    """The adapter got a response it doesn't know how to handle."""
