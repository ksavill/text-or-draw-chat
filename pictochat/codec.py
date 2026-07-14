"""PictoChat application-layer records and transfer primitives.

The formats here are capture-derived, not an official Nintendo specification.
Unknown bytes are represented explicitly and retained during decode/encode
round trips. See ``pictochat/ASSUMPTIONS.md`` before transmitting records to a
real console.

This module does not implement the lower Nintendo local-multiplayer (Ni-Fi)
MAC protocol. In particular, it does not generate beacons or time CF-Poll and
CF-Ack frames.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import struct
from typing import Iterable, Sequence


BACKING_WIDTH = 256
VISIBLE_WIDTH = 228
VISIBLE_HEIGHT = 80
TILE_SIZE = 8
ROW_HEIGHT = 16
MAX_IMAGE_ROWS = 5
IMAGE_BYTES_PER_ROW = 2048  # 256 * 16 pixels at four bits per pixel

PROFILE_SIZE = 84
DRAWING_HEADER_SIZE = 36

# Seen in the current foa_dswifi PictoChat implementation. Captures indicate
# that changing these bytes does not affect rendering, but their semantics are
# not known. Callers can and should replace them when replaying a golden trace.
CAPTURED_DRAWING_METADATA = bytes(
    (0x00, 0x05, 0x00, 0x00, 0x00, 0x00, 0x03,
     0x06, 0x08, 0x0D, 0x08, 0x0D, 0x12, 0x1B)
)

_COMMON_HEADER = struct.Struct("<HH")
_TYPE1_BODY = struct.Struct("<BB2sH10s")
_TYPE2_FIXED_BODY = struct.Struct("<BBBBH2s")


class CodecError(ValueError):
    """Raised when bytes or values cannot represent a supported record."""


def _u8(value: int, name: str) -> int:
    if not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise CodecError(f"{name} must be an integer from 0 to 255")
    return value


def _u16(value: int, name: str) -> int:
    if not isinstance(value, int) or not 0 <= value <= 0xFFFF:
        raise CodecError(f"{name} must be an integer from 0 to 65535")
    return value


def _fixed_bytes(value: bytes | bytearray | memoryview, size: int,
                 name: str) -> bytes:
    result = bytes(value)
    if len(result) != size:
        raise CodecError(f"{name} must be exactly {size} bytes")
    return result


def _mac_bytes(value: bytes | bytearray | memoryview) -> bytes:
    return _fixed_bytes(value, 6, "MAC address")


def pair_swap_mac(mac: bytes | bytearray | memoryview) -> bytes:
    """Swap the bytes within each 16-bit pair of a six-byte MAC address.

    PictoChat stores application-record MACs as ``[1,0,3,2,5,4]`` relative
    to their conventional byte order. Calling this function twice restores the
    input.
    """

    raw = _mac_bytes(mac)
    return bytes((raw[1], raw[0], raw[3], raw[2], raw[5], raw[4]))


def _encode_fixed_text(text: str, byte_length: int, name: str) -> bytes:
    if "\x00" in text:
        raise CodecError(f"{name} cannot contain a NUL character")
    try:
        encoded = text.encode("utf-16-le")
    except UnicodeEncodeError as exc:
        raise CodecError(f"{name} cannot be encoded as UTF-16LE") from exc
    if len(encoded) > byte_length:
        raise CodecError(
            f"{name} exceeds {byte_length // 2} UTF-16 code units"
        )
    return encoded.ljust(byte_length, b"\x00")


def _decode_fixed_text(raw: bytes, name: str) -> str:
    if len(raw) % 2:
        raise CodecError(f"{name} field has an odd byte length")
    end = len(raw)
    for offset in range(0, len(raw), 2):
        if raw[offset:offset + 2] == b"\x00\x00":
            end = offset
            break
    try:
        return raw[:end].decode("utf-16-le")
    except UnicodeDecodeError as exc:
        raise CodecError(f"{name} is not valid UTF-16LE/UCS-2 data") from exc


@dataclass(frozen=True, slots=True)
class ProfileRecord:
    """An 84-byte PictoChat user/profile application record.

    ``subtype`` is observed as zero or one during the identity handshake. The
    two bytes at offsets 82 and 83 are interpreted as month then day based on
    current capture dissectors. The record's MAC is exposed in conventional
    byte order and pair-swapped on the wire.
    """

    subject_mac: bytes
    nickname: str = ""
    personal_message: str = ""
    colour: int = 0
    birth_month: int = 1
    birth_day: int = 1
    subtype: int = 0
    magic: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_mac", _mac_bytes(self.subject_mac))
        _u8(self.magic, "magic")
        _u8(self.subtype, "subtype")
        _u16(self.colour, "colour")
        _u8(self.birth_month, "birth_month")
        _u8(self.birth_day, "birth_day")

    def to_bytes(self) -> bytes:
        nickname = _encode_fixed_text(self.nickname, 20, "nickname")
        personal = _encode_fixed_text(
            self.personal_message, 52, "personal_message"
        )
        return b"".join((
            bytes((_u8(self.magic, "magic"), _u8(self.subtype, "subtype"))),
            pair_swap_mac(self.subject_mac),
            nickname,
            personal,
            struct.pack("<H", _u16(self.colour, "colour")),
            bytes((
                _u8(self.birth_month, "birth_month"),
                _u8(self.birth_day, "birth_day"),
            )),
        ))

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> ProfileRecord:
        raw = bytes(data)
        if len(raw) != PROFILE_SIZE:
            raise CodecError(
                f"profile record must be exactly {PROFILE_SIZE} bytes"
            )
        return cls(
            magic=raw[0],
            subtype=raw[1],
            subject_mac=pair_swap_mac(raw[2:8]),
            nickname=_decode_fixed_text(raw[8:28], "nickname"),
            personal_message=_decode_fixed_text(
                raw[28:80], "personal_message"
            ),
            colour=struct.unpack_from("<H", raw, 80)[0],
            birth_month=raw[82],
            birth_day=raw[83],
        )


def encode_profile(profile: ProfileRecord) -> bytes:
    """Encode a :class:`ProfileRecord` to its 84-byte wire form."""

    return profile.to_bytes()


def decode_profile(data: bytes | bytearray | memoryview) -> ProfileRecord:
    """Decode an 84-byte wire profile."""

    return ProfileRecord.from_bytes(data)


def _normalise_pixels(
    pixels: Sequence[Sequence[int]], *, expected_width: int,
) -> list[list[int]]:
    rows = [list(row) for row in pixels]
    if len(rows) not in tuple(ROW_HEIGHT * n for n in range(1, 6)):
        raise CodecError("image height must be 16, 32, 48, 64, or 80 pixels")
    for y, row in enumerate(rows):
        if len(row) != expected_width:
            raise CodecError(
                f"pixel row {y} must contain exactly {expected_width} pixels"
            )
        for x, value in enumerate(row):
            if not isinstance(value, int) or not 0 <= value <= 0x0F:
                raise CodecError(
                    f"pixel ({x}, {y}) must be a palette index from 0 to 15"
                )
    return rows


def encode_tiled_image(pixels: Sequence[Sequence[int]]) -> bytes:
    """Encode a 256-wide palette-index image into PictoChat 4-bpp tiles.

    Tiles are 8x8 and appear in row-major order. Within each byte the left
    pixel occupies the low nibble and the right pixel occupies the high nibble.
    The height must be one to five 16-pixel PictoChat rows.
    """

    rows = _normalise_pixels(pixels, expected_width=BACKING_WIDTH)
    output = bytearray(BACKING_WIDTH * len(rows) // 2)
    offset = 0
    for tile_y in range(0, len(rows), TILE_SIZE):
        for tile_x in range(0, BACKING_WIDTH, TILE_SIZE):
            for y in range(tile_y, tile_y + TILE_SIZE):
                for x in range(tile_x, tile_x + TILE_SIZE, 2):
                    output[offset] = rows[y][x] | (rows[y][x + 1] << 4)
                    offset += 1
    return bytes(output)


def decode_tiled_image(
    data: bytes | bytearray | memoryview,
) -> list[list[int]]:
    """Decode PictoChat tiled bytes into rows of 256 palette indices."""

    raw = bytes(data)
    if (not raw or len(raw) % IMAGE_BYTES_PER_ROW != 0 or
            len(raw) > IMAGE_BYTES_PER_ROW * MAX_IMAGE_ROWS):
        raise CodecError(
            "tiled image must contain 2048, 4096, 6144, 8192, or 10240 bytes"
        )
    height = len(raw) * 2 // BACKING_WIDTH
    rows = [[0] * BACKING_WIDTH for _ in range(height)]
    offset = 0
    for tile_y in range(0, height, TILE_SIZE):
        for tile_x in range(0, BACKING_WIDTH, TILE_SIZE):
            for y in range(tile_y, tile_y + TILE_SIZE):
                for x in range(tile_x, tile_x + TILE_SIZE, 2):
                    packed = raw[offset]
                    offset += 1
                    rows[y][x] = packed & 0x0F
                    rows[y][x + 1] = packed >> 4
    return rows


def visible_to_backing(
    pixels: Sequence[Sequence[int]], *, x_offset: int = 0, fill: int = 0,
) -> list[list[int]]:
    """Place a 228-wide browser image in the 256-wide transmitted backing.

    ``x_offset=0`` is an explicit provisional mapping, not a claim about the
    DS UI mask. Callers can select any offset from 0 through 28 after comparing
    against a golden hardware capture. Pixels outside the visible rectangle
    are filled with ``fill``.
    """

    rows = _normalise_pixels(pixels, expected_width=VISIBLE_WIDTH)
    if not isinstance(x_offset, int) or not 0 <= x_offset <= BACKING_WIDTH - VISIBLE_WIDTH:
        raise CodecError("x_offset must be from 0 to 28")
    if not isinstance(fill, int) or not 0 <= fill <= 0x0F:
        raise CodecError("fill must be a palette index from 0 to 15")
    backing = [[fill] * BACKING_WIDTH for _ in rows]
    for y, row in enumerate(rows):
        backing[y][x_offset:x_offset + VISIBLE_WIDTH] = row
    return backing


def backing_to_visible(
    pixels: Sequence[Sequence[int]], *, x_offset: int = 0,
) -> list[list[int]]:
    """Crop a 228-wide browser view from the 256-wide transmitted backing."""

    rows = _normalise_pixels(pixels, expected_width=BACKING_WIDTH)
    if not isinstance(x_offset, int) or not 0 <= x_offset <= BACKING_WIDTH - VISIBLE_WIDTH:
        raise CodecError("x_offset must be from 0 to 28")
    return [row[x_offset:x_offset + VISIBLE_WIDTH] for row in rows]


def encode_visible_image(
    pixels: Sequence[Sequence[int]], *, x_offset: int = 0, fill: int = 0,
) -> bytes:
    """Pad and tile-encode a 228-wide browser image."""

    return encode_tiled_image(
        visible_to_backing(pixels, x_offset=x_offset, fill=fill)
    )


def decode_visible_image(
    data: bytes | bytearray | memoryview, *, x_offset: int = 0,
) -> list[list[int]]:
    """Decode a tiled backing image and crop it to 228 pixels wide."""

    return backing_to_visible(decode_tiled_image(data), x_offset=x_offset)


@dataclass(frozen=True, slots=True)
class DrawingRecord:
    """A reassembled PictoChat drawing record.

    ``metadata`` and ``safezone`` are the two capture-derived 14-byte regions
    between the author MAC and image. Their semantics are not established, so
    decoded bytes are retained and encoding permits caller-provided values.
    """

    author_mac: bytes
    image: bytes
    metadata: bytes = CAPTURED_DRAWING_METADATA
    safezone: bytes = bytes(14)
    magic: int = 3
    subtype: int = 2

    def __post_init__(self) -> None:
        object.__setattr__(self, "author_mac", _mac_bytes(self.author_mac))
        object.__setattr__(self, "image", bytes(self.image))
        object.__setattr__(
            self, "metadata", _fixed_bytes(self.metadata, 14, "metadata")
        )
        object.__setattr__(
            self, "safezone", _fixed_bytes(self.safezone, 14, "safezone")
        )
        _u8(self.magic, "magic")
        _u8(self.subtype, "subtype")
        # Validate image size and tile structure without retaining a duplicate.
        decode_tiled_image(self.image)

    @property
    def row_count(self) -> int:
        return len(self.image) // IMAGE_BYTES_PER_ROW

    @property
    def height(self) -> int:
        return self.row_count * ROW_HEIGHT

    def pixels(self) -> list[list[int]]:
        return decode_tiled_image(self.image)

    def visible_pixels(self, *, x_offset: int = 0) -> list[list[int]]:
        return decode_visible_image(self.image, x_offset=x_offset)

    def to_bytes(self) -> bytes:
        return b"".join((
            bytes((_u8(self.magic, "magic"), _u8(self.subtype, "subtype"))),
            pair_swap_mac(self.author_mac),
            self.metadata,
            self.safezone,
            self.image,
        ))

    @classmethod
    def from_bytes(cls, data: bytes | bytearray | memoryview) -> DrawingRecord:
        raw = bytes(data)
        if len(raw) < DRAWING_HEADER_SIZE:
            raise CodecError("drawing record is shorter than its 36-byte header")
        return cls(
            magic=raw[0],
            subtype=raw[1],
            author_mac=pair_swap_mac(raw[2:8]),
            metadata=raw[8:22],
            safezone=raw[22:36],
            image=raw[36:],
        )

    @classmethod
    def from_pixels(
        cls, author_mac: bytes, pixels: Sequence[Sequence[int]], **kwargs: object,
    ) -> DrawingRecord:
        return cls(author_mac=author_mac, image=encode_tiled_image(pixels), **kwargs)

    @classmethod
    def from_visible(
        cls, author_mac: bytes, pixels: Sequence[Sequence[int]], *,
        x_offset: int = 0, fill: int = 0, **kwargs: object,
    ) -> DrawingRecord:
        return cls(
            author_mac=author_mac,
            image=encode_visible_image(
                pixels, x_offset=x_offset, fill=fill
            ),
            **kwargs,
        )


@dataclass(frozen=True, slots=True)
class TransferAnnouncement:
    """The 20-byte Type 1 transfer announcement/request.

    The final ten bytes include fields whose meanings are not fully known and
    may contain a transfer identifier. They are never generated implicitly.
    Supply bytes from a validated implementation or golden capture.

    Client-to-host captures may use Type ID zero for the same body, with the
    host echoing it as Type ID one. Both values are retained by this codec.
    """

    sender_id: int
    data_type: int
    data_size: int
    trailer: bytes
    marker: bytes = b"\xff\xff"
    type_id: int = 1

    def __post_init__(self) -> None:
        _u8(self.sender_id, "sender_id")
        _u8(self.data_type, "data_type")
        _u16(self.data_size, "data_size")
        object.__setattr__(self, "marker", _fixed_bytes(self.marker, 2, "marker"))
        object.__setattr__(self, "trailer", _fixed_bytes(self.trailer, 10, "trailer"))
        if self.type_id not in (0, 1):
            raise CodecError("announcement type_id must be 0 or 1")

    def to_bytes(self) -> bytes:
        return _COMMON_HEADER.pack(self.type_id, 20) + _TYPE1_BODY.pack(
            self.sender_id,
            self.data_type,
            self.marker,
            self.data_size,
            self.trailer,
        )

    @classmethod
    def from_bytes(
        cls, data: bytes | bytearray | memoryview,
    ) -> TransferAnnouncement:
        raw = bytes(data)
        if len(raw) != 20:
            raise CodecError("Type 1 announcement must be exactly 20 bytes")
        type_id, declared_size = _COMMON_HEADER.unpack_from(raw)
        if type_id not in (0, 1):
            raise CodecError(f"expected Type 0/1 announcement, got Type {type_id}")
        if declared_size != len(raw):
            raise CodecError(
                f"announcement declares {declared_size} bytes, got {len(raw)}"
            )
        sender, data_type, marker, size, trailer = _TYPE1_BODY.unpack_from(raw, 4)
        return cls(
            sender_id=sender,
            data_type=data_type,
            data_size=size,
            trailer=trailer,
            marker=marker,
            type_id=type_id,
        )


@dataclass(frozen=True, slots=True)
class TransferFragment:
    """A Type 2 transfer fragment.

    Bit zero of ``transfer_flags`` is treated as the final-fragment marker by
    the reassembler because current captures and foa_dswifi use values 0/1.
    Other flag bits and ``payload_type`` are preserved.
    """

    sending_console_id: int
    payload_type: int
    transfer_flags: int
    write_offset: int
    payload: bytes
    magic: bytes = b"\x00\x00"

    def __post_init__(self) -> None:
        _u8(self.sending_console_id, "sending_console_id")
        _u8(self.payload_type, "payload_type")
        _u8(self.transfer_flags, "transfer_flags")
        _u16(self.write_offset, "write_offset")
        object.__setattr__(self, "payload", bytes(self.payload))
        object.__setattr__(self, "magic", _fixed_bytes(self.magic, 2, "magic"))
        if len(self.payload) > 0xFF:
            raise CodecError("fragment payload cannot exceed 255 bytes")
        if self.write_offset + len(self.payload) > 0x10000:
            raise CodecError("fragment end exceeds the 16-bit transfer address space")

    @property
    def is_final(self) -> bool:
        return bool(self.transfer_flags & 0x01)

    def to_bytes(self) -> bytes:
        total_size = 12 + len(self.payload)
        return b"".join((
            _COMMON_HEADER.pack(2, total_size),
            _TYPE2_FIXED_BODY.pack(
                self.sending_console_id,
                self.payload_type,
                len(self.payload),
                self.transfer_flags,
                self.write_offset,
                self.magic,
            ),
            self.payload,
        ))

    @classmethod
    def from_bytes(
        cls, data: bytes | bytearray | memoryview,
    ) -> TransferFragment:
        raw = bytes(data)
        if len(raw) < 12:
            raise CodecError("Type 2 fragment is shorter than its 12-byte header")
        type_id, declared_size = _COMMON_HEADER.unpack_from(raw)
        if type_id != 2:
            raise CodecError(f"expected Type 2 fragment, got Type {type_id}")
        if declared_size != len(raw):
            raise CodecError(
                f"fragment declares {declared_size} bytes, got {len(raw)}"
            )
        (console_id, payload_type, payload_length, flags,
         write_offset, magic) = _TYPE2_FIXED_BODY.unpack_from(raw, 4)
        if 12 + payload_length != len(raw):
            raise CodecError(
                f"fragment payload declares {payload_length} bytes, "
                f"got {len(raw) - 12}"
            )
        return cls(
            sending_console_id=console_id,
            payload_type=payload_type,
            transfer_flags=flags,
            write_offset=write_offset,
            magic=magic,
            payload=raw[12:],
        )


def fragment_payload(
    payload: bytes | bytearray | memoryview, *,
    sending_console_id: int,
    chunk_size: int = 180,
    magic: bytes = b"\x00\x00",
    first_payload_type: int = 0xFF,
    middle_payload_type: int = 0x97,
    final_payload_type: int = 0x04,
) -> tuple[TransferFragment, ...]:
    """Split a reassembled record into current capture-derived Type 2 chunks.

    The defaults match the working foa_dswifi experiment, not an official
    specification: first/middle/final payload types are FF/97/04, and the final
    fragment has transfer flag bit zero set. Set these arguments from a known
    hardware trace if its values differ.
    """

    raw = bytes(payload)
    if len(raw) > 0xFFFF:
        raise CodecError("transfer payload cannot exceed 65535 bytes")
    if not isinstance(chunk_size, int) or not 1 <= chunk_size <= 0xFF:
        raise CodecError("chunk_size must be from 1 to 255")
    _u8(sending_console_id, "sending_console_id")
    _u8(first_payload_type, "first_payload_type")
    _u8(middle_payload_type, "middle_payload_type")
    _u8(final_payload_type, "final_payload_type")
    magic = _fixed_bytes(magic, 2, "magic")

    fragments: list[TransferFragment] = []
    offsets = range(0, len(raw), chunk_size) if raw else (0,)
    for offset in offsets:
        part = raw[offset:offset + chunk_size]
        final = offset + len(part) >= len(raw)
        if final:
            payload_type = final_payload_type
        elif offset == 0:
            payload_type = first_payload_type
        else:
            payload_type = middle_payload_type
        fragments.append(TransferFragment(
            sending_console_id=sending_console_id,
            payload_type=payload_type,
            transfer_flags=1 if final else 0,
            write_offset=offset,
            magic=magic,
            payload=part,
        ))
    return tuple(fragments)


def build_transfer(
    payload: bytes | bytearray | memoryview, *,
    sender_id: int,
    data_type: int,
    announcement_trailer: bytes,
    chunk_size: int = 180,
) -> tuple[TransferAnnouncement, tuple[TransferFragment, ...]]:
    """Build an announcement and fragments for one application record.

    ``announcement_trailer`` is required because ten bytes of Type 1 state are
    not sufficiently understood to synthesize safely.
    """

    raw = bytes(payload)
    announcement = TransferAnnouncement(
        sender_id=sender_id,
        data_type=data_type,
        data_size=len(raw),
        trailer=announcement_trailer,
    )
    return announcement, fragment_payload(
        raw, sending_console_id=sender_id, chunk_size=chunk_size
    )


@dataclass(slots=True)
class TransferReassembler:
    """Offset-based Type 2 reassembly with retransmission deduplication."""

    expected_size: int
    sending_console_id: int | None = None
    _data: bytearray = field(init=False, repr=False)
    _present: bytearray = field(init=False, repr=False)
    _bytes_received: int = field(init=False, default=0, repr=False)
    _saw_final: bool = field(init=False, default=False, repr=False)
    duplicate_count: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        _u16(self.expected_size, "expected_size")
        if self.sending_console_id is not None:
            _u8(self.sending_console_id, "sending_console_id")
        self._data = bytearray(self.expected_size)
        self._present = bytearray(self.expected_size)

    @classmethod
    def from_announcement(
        cls, announcement: TransferAnnouncement,
    ) -> TransferReassembler:
        return cls(
            expected_size=announcement.data_size,
            sending_console_id=announcement.sender_id,
        )

    @property
    def bytes_received(self) -> int:
        return self._bytes_received

    @property
    def saw_final(self) -> bool:
        return self._saw_final

    @property
    def has_all_bytes(self) -> bool:
        return self._bytes_received == self.expected_size

    @property
    def complete(self) -> bool:
        return self.has_all_bytes and self._saw_final

    def add(self, fragment: TransferFragment) -> bool:
        """Add a fragment, returning false for an exact retransmission.

        Consistent partial overlaps are accepted. A conflicting overlap,
        sender mismatch, overflow, or final fragment ending at the wrong
        declared transfer size raises :class:`CodecError`.
        """

        if self.sending_console_id is None:
            self.sending_console_id = fragment.sending_console_id
        elif fragment.sending_console_id != self.sending_console_id:
            raise CodecError(
                "fragment sender does not match the active transfer"
            )

        start = fragment.write_offset
        end = start + len(fragment.payload)
        if end > self.expected_size:
            raise CodecError(
                f"fragment [{start}, {end}) exceeds transfer size "
                f"{self.expected_size}"
            )
        if fragment.is_final and end != self.expected_size:
            raise CodecError(
                "final fragment does not end at the announced transfer size"
            )

        # A first zero-length final marker is meaningful for a zero-byte
        # transfer even though it contributes no payload bytes.
        added = fragment.is_final and not self._saw_final
        for index, value in enumerate(fragment.payload, start):
            if self._present[index]:
                if self._data[index] != value:
                    raise CodecError(
                        f"conflicting retransmission at transfer offset {index}"
                    )
            else:
                self._data[index] = value
                self._present[index] = 1
                self._bytes_received += 1
                added = True

        if fragment.is_final:
            self._saw_final = True
        if not added:
            self.duplicate_count += 1
        return added

    def assemble(self, *, allow_without_final: bool = False) -> bytes:
        """Return the record once every byte is present and final was seen."""

        if not self.has_all_bytes:
            raise CodecError(
                f"transfer is incomplete: received {self.bytes_received} of "
                f"{self.expected_size} bytes"
            )
        if not self.saw_final and not allow_without_final:
            raise CodecError("transfer has all bytes but no final fragment")
        return bytes(self._data)


def reassemble_transfer(
    announcement: TransferAnnouncement,
    fragments: Iterable[TransferFragment],
) -> bytes:
    """Reassemble and deduplicate fragments using a Type 1 announcement."""

    reassembler = TransferReassembler.from_announcement(announcement)
    for fragment in fragments:
        reassembler.add(fragment)
    return reassembler.assemble()
