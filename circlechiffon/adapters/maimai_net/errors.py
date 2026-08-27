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


class TransientNetError(MaimaiNetError):
    """maimai DX NET served its error page for a reason that clears by itself
    on a retry - the classic one being "Connection time has expired, please
    try again later", which a browser user gets past by hitting refresh (or
    backing up and re-entering the page). The session cookie is still valid,
    which is exactly what separates this from SessionExpired: raising
    SessionExpired here would send the user through a pointless /cc-login,
    or burn a silent re-login for a remember_password account.

    `code` is the "ERROR CODE：NNNNNN" number off the error page when it could
    be parsed. It's carried on the exception (and folded into the message)
    because the adapter has no logging - surfacing it through the cogs'
    existing `except MaimaiNetError as e` arm is the only way an unrecognized
    code becomes visible.

    `session_suspect` marks an error that *might* be a dead session rather
    than a wedged one (DX NET's 200002 is both - see
    urls.SESSION_SUSPECT_ERROR_CODES). It is still retried, but if it
    survives every attempt, _get_page converts it to SessionExpired so the
    user reaches /cc-login instead of being told "busy" forever.
    """

    def __init__(self, message: str, code: str | None = None, session_suspect: bool = False):
        super().__init__(message)
        self.code = code
        self.session_suspect = session_suspect


class ItemNotOwned(MaimaiNetError):
    """A saved preset names a collection item the account no longer has (or
    never had) on the relevant listing page."""
