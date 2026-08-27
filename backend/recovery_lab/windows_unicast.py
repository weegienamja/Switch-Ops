"""Typed bindings for the Windows IP Helper unicast address APIs.

Development-only. Nothing in ``backend.app`` imports this module, and it has no
API route. It exists so the temporary-address recovery primitive can be
validated against real Windows behaviour *before* SwitchOps is ever given
authority to use it.

Why this API rather than ``New-NetIPAddress`` / WMI ``EnableStatic``:
``EnableStatic`` replaces an interface's addressing configuration and therefore
disables DHCP on it. ``CreateUnicastIpAddressEntry`` adds one row to the
unicast address table, leaving the DHCP-learned row untouched. That difference
is the whole reason this primitive is a candidate at all -- but it is a claim to
be *verified* by re-reading the DHCP row after an operation, not assumed.

Two measured facts drive the design here:

* ``InitializeUnicastIpAddressEntry`` leaves ``OnLinkPrefixLength`` at 255,
  which Windows turns into a /32. An executor that forgot to set it would
  create an address with no on-link route to the management prefix -- the
  recovery would look successful and still not reach the device. This module
  therefore requires the prefix length explicitly.
* Unelevated callers get ``ERROR_ACCESS_DENIED``. The primitive fails closed,
  so a non-elevated SwitchOps backend cannot mutate host addressing at all.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes  # noqa: F401  (ensures wintypes is initialised on Windows)
import ipaddress
import sys
from dataclasses import dataclass
from typing import Literal

# --- Windows constants -----------------------------------------------------

NO_ERROR = 0
ERROR_ACCESS_DENIED = 5
ERROR_NOT_SUPPORTED = 50
ERROR_INVALID_PARAMETER = 87
ERROR_NOT_FOUND = 1168
ERROR_OBJECT_ALREADY_EXISTS = 5010

ERROR_NAMES = {
    NO_ERROR: "NO_ERROR",
    ERROR_ACCESS_DENIED: "ERROR_ACCESS_DENIED",
    ERROR_NOT_SUPPORTED: "ERROR_NOT_SUPPORTED",
    ERROR_INVALID_PARAMETER: "ERROR_INVALID_PARAMETER",
    ERROR_NOT_FOUND: "ERROR_NOT_FOUND",
    ERROR_OBJECT_ALREADY_EXISTS: "ERROR_OBJECT_ALREADY_EXISTS",
}

AF_INET = 2
AF_UNSPEC = 0

#: NL_DAD_STATE. Only ``PREFERRED`` means the address is usable.
DAD_STATE = {
    0: "INVALID",
    1: "TENTATIVE",
    2: "DUPLICATE",
    3: "DEPRECATED",
    4: "PREFERRED",
}

#: NL_PREFIX_ORIGIN / NL_SUFFIX_ORIGIN. ``DHCP`` on the primary row is the
#: evidence that a DHCP lease is still in force.
PREFIX_ORIGIN = {
    0: "OTHER",
    1: "MANUAL",
    2: "WELL_KNOWN",
    3: "DHCP",
    4: "ROUTER_ADVERTISEMENT",
    16: "UNCHANGED",
}
SUFFIX_ORIGIN = {
    0: "OTHER",
    1: "MANUAL",
    2: "WELL_KNOWN",
    3: "DHCP",
    4: "LINK_LAYER_ADDRESS",
    5: "RANDOM",
    16: "UNCHANGED",
}

DadState = Literal["INVALID", "TENTATIVE", "DUPLICATE", "DEPRECATED", "PREFERRED"]


# --- Structures ------------------------------------------------------------


class _SockaddrIn(ctypes.Structure):
    _fields_ = [
        ("sin_family", ctypes.c_ushort),
        ("sin_port", ctypes.c_ushort),
        ("sin_addr", ctypes.c_ubyte * 4),
        ("sin_zero", ctypes.c_ubyte * 8),
    ]


class _SockaddrInet(ctypes.Union):
    _fields_ = [("Ipv4", _SockaddrIn), ("raw", ctypes.c_ubyte * 28)]


class MibUnicastIpAddressRow(ctypes.Structure):
    """MIB_UNICASTIPADDRESS_ROW. Measured at 80 bytes on x64."""

    _fields_ = [
        ("Address", _SockaddrInet),
        ("InterfaceLuid", ctypes.c_uint64),
        ("InterfaceIndex", ctypes.c_uint32),
        ("PrefixOrigin", ctypes.c_int),
        ("SuffixOrigin", ctypes.c_int),
        ("ValidLifetime", ctypes.c_uint32),
        ("PreferredLifetime", ctypes.c_uint32),
        ("OnLinkPrefixLength", ctypes.c_ubyte),
        ("SkipAsSource", ctypes.c_ubyte),
        ("DadState", ctypes.c_int),
        ("ScopeId", ctypes.c_uint32),
        ("CreationTimeStamp", ctypes.c_int64),
    ]


@dataclass(frozen=True)
class UnicastAddress:
    """One decoded row, in terms SwitchOps reasons about."""

    address: str
    prefix_length: int
    interface_index: int
    interface_luid: int
    prefix_origin: str
    suffix_origin: str
    dad_state: str
    valid_lifetime: int
    preferred_lifetime: int
    skip_as_source: bool

    @property
    def is_dhcp(self) -> bool:
        """True when Windows still considers this a DHCP-assigned address."""
        return self.prefix_origin == "DHCP" and self.suffix_origin == "DHCP"

    @property
    def is_usable(self) -> bool:
        return self.dad_state == "PREFERRED"

    @property
    def has_finite_lease(self) -> bool:
        """A DHCP lease has a finite lifetime; a manual address does not."""
        return self.valid_lifetime != 0xFFFFFFFF


class UnsupportedPlatform(RuntimeError):
    """The IP Helper unicast APIs exist on Windows only."""


def is_supported() -> bool:
    return sys.platform == "win32"


def _iphlpapi() -> ctypes.WinDLL:  # type: ignore[name-defined]
    if not is_supported():
        raise UnsupportedPlatform(
            "The IP Helper unicast address APIs are available on Windows only."
        )
    return ctypes.WinDLL("iphlpapi.dll")  # type: ignore[attr-defined]


def describe_error(code: int) -> str:
    return ERROR_NAMES.get(code, f"WIN32_ERROR_{code}")


# --- Read-only operations --------------------------------------------------


def read_unicast_table() -> list[UnicastAddress]:
    """Return every IPv4 unicast address. Read-only; no elevation required."""
    dll = _iphlpapi()
    table = ctypes.c_void_p()
    rc = dll.GetUnicastIpAddressTable(ctypes.c_ushort(AF_INET), ctypes.byref(table))
    if rc != NO_ERROR:
        raise OSError(rc, f"GetUnicastIpAddressTable failed: {describe_error(rc)}")

    try:
        count = ctypes.c_uint32.from_address(table.value).value
        row_size = ctypes.sizeof(MibUnicastIpAddressRow)
        # x64: ULONG NumEntries, 4 bytes padding, then 8-aligned rows.
        base = table.value + 8
        rows: list[UnicastAddress] = []
        for index in range(count):
            row = MibUnicastIpAddressRow.from_address(base + index * row_size)
            if row.Address.Ipv4.sin_family != AF_INET:
                continue
            rows.append(_decode(row))
        return rows
    finally:
        dll.FreeMibTable(table)


def _decode(row: MibUnicastIpAddressRow) -> UnicastAddress:
    octets = bytes(row.Address.Ipv4.sin_addr)
    return UnicastAddress(
        address=str(ipaddress.IPv4Address(octets)),
        prefix_length=int(row.OnLinkPrefixLength),
        interface_index=int(row.InterfaceIndex),
        interface_luid=int(row.InterfaceLuid),
        prefix_origin=PREFIX_ORIGIN.get(row.PrefixOrigin, str(row.PrefixOrigin)),
        suffix_origin=SUFFIX_ORIGIN.get(row.SuffixOrigin, str(row.SuffixOrigin)),
        dad_state=DAD_STATE.get(row.DadState, str(row.DadState)),
        valid_lifetime=int(row.ValidLifetime) & 0xFFFFFFFF,
        preferred_lifetime=int(row.PreferredLifetime) & 0xFFFFFFFF,
        skip_as_source=bool(row.SkipAsSource),
    )


def find_address(address: str, interface_index: int) -> UnicastAddress | None:
    """Look up one exact address on one exact interface."""
    for row in read_unicast_table():
        if row.address == address and row.interface_index == interface_index:
            return row
    return None


def is_elevated() -> bool:
    """True when this process can call the mutating IP Helper entry points."""
    if not is_supported():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:
        return False


# --- Mutating operations ---------------------------------------------------
#
# These are the only functions in the repository that can change host
# addressing. They are unreachable from the product: nothing under
# ``backend.app`` imports this module, and the packaged sidecar runs
# unelevated, so the OS refuses the call regardless.


def _build_row(
    *, address: str, prefix_length: int, interface_index: int, interface_luid: int
) -> MibUnicastIpAddressRow:
    parsed = ipaddress.IPv4Address(address)
    if not 0 < prefix_length <= 32:
        raise ValueError("prefix_length must be between 1 and 32.")

    dll = _iphlpapi()
    row = MibUnicastIpAddressRow()
    dll.InitializeUnicastIpAddressEntry(ctypes.byref(row))

    if interface_luid <= 0:
        raise ValueError("interface_luid must identify the exact adapter.")
    # Microsoft specifies that LUID takes precedence over ifIndex. Supplying
    # both binds the operation to the durable local interface identity while
    # retaining the observed index for diagnostics.
    row.InterfaceLuid = ctypes.c_uint64(interface_luid).value
    row.InterfaceIndex = ctypes.c_uint32(interface_index).value
    row.Address.Ipv4.sin_family = AF_INET
    row.Address.Ipv4.sin_addr[:] = parsed.packed
    # Explicit, never the initialised 255: that would silently produce a /32
    # with no on-link route to the management prefix.
    row.OnLinkPrefixLength = prefix_length
    # DadState is ignored on create *unless* it is set to PREFERRED, which asks
    # Windows for optimistic DAD. That would skip the collision proof this
    # whole design depends on, so it is deliberately left alone.
    return row


def create_temporary_address(
    *, address: str, prefix_length: int, interface_index: int, interface_luid: int
) -> int:
    """Add one temporary unicast address. Returns a Win32 code.

    The address is not persistent: Windows destroys it on reboot, NIC reset,
    and some PnP events. It does *not* die with the creating process, which is
    why callers need a durable journal.
    """
    row = _build_row(
        address=address,
        prefix_length=prefix_length,
        interface_index=interface_index,
        interface_luid=interface_luid,
    )
    return int(_iphlpapi().CreateUnicastIpAddressEntry(ctypes.byref(row)))


def delete_temporary_address(
    *, address: str, prefix_length: int, interface_index: int, interface_luid: int
) -> int:
    """Delete exactly one address on exactly one interface.

    Targeting a single row is the point. There is no "remove everything in this
    prefix" path here, because that would eventually remove somebody else's
    address during a rollback.
    """
    row = _build_row(
        address=address,
        prefix_length=prefix_length,
        interface_index=interface_index,
        interface_luid=interface_luid,
    )
    return int(_iphlpapi().DeleteUnicastIpAddressEntry(ctypes.byref(row)))
