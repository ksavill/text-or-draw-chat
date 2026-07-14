import struct

import pytest

from pictochat import (
    BACKING_WIDTH,
    CAPTURED_DRAWING_METADATA,
    CodecError,
    DrawingRecord,
    ProfileRecord,
    TransferAnnouncement,
    TransferFragment,
    TransferReassembler,
    backing_to_visible,
    build_transfer,
    decode_tiled_image,
    decode_visible_image,
    encode_tiled_image,
    encode_visible_image,
    fragment_payload,
    pair_swap_mac,
    reassemble_transfer,
    visible_to_backing,
)


def pixels(width=BACKING_WIDTH, height=16, value=0):
    return [[value for _ in range(width)] for _ in range(height)]


def test_mac_pair_swap_is_wire_order_and_self_inverse():
    mac = bytes.fromhex("0009bf123456")
    wire = pair_swap_mac(mac)
    assert wire == bytes.fromhex("090012bf5634")
    assert pair_swap_mac(wire) == mac


def test_profile_record_exact_layout_and_round_trip():
    profile = ProfileRecord(
        subject_mac=bytes.fromhex("0009bf123456"),
        nickname="Picto☆",
        personal_message="hello",
        colour=15,
        birth_month=7,
        birth_day=13,
        subtype=1,
    )
    wire = profile.to_bytes()

    assert len(wire) == 84
    assert wire[:8] == bytes.fromhex("0301090012bf5634")
    assert wire[8:14] == "Pic".encode("utf-16-le")
    assert struct.unpack_from("<H", wire, 80)[0] == 15
    assert wire[82:] == bytes((7, 13))
    assert ProfileRecord.from_bytes(wire) == profile


def test_profile_rejects_ambiguous_or_overlong_text():
    base = dict(subject_mac=bytes(6))
    with pytest.raises(CodecError, match="NUL"):
        ProfileRecord(**base, nickname="a\x00b").to_bytes()
    with pytest.raises(CodecError, match="10 UTF-16 code units"):
        ProfileRecord(**base, nickname="12345678901").to_bytes()
    with pytest.raises(CodecError, match="exactly 84"):
        ProfileRecord.from_bytes(bytes(83))


def test_tiled_encoding_uses_low_nibble_first_and_tile_major_order():
    image = pixels()
    image[0][:8] = list(range(1, 9))
    image[0][8:10] = [9, 10]
    image[8][0:2] = [11, 12]

    encoded = encode_tiled_image(image)

    assert len(encoded) == 2048
    assert encoded[:4] == bytes((0x21, 0x43, 0x65, 0x87))
    assert encoded[32] == 0xA9  # next horizontal 8x8 tile
    assert encoded[1024] == 0xCB  # next vertical tile row
    assert decode_tiled_image(encoded) == image


@pytest.mark.parametrize("height", [16, 32, 48, 64, 80])
def test_all_supported_tiled_image_heights_round_trip(height):
    image = [
        [((x // 7) + y) & 0xF for x in range(BACKING_WIDTH)]
        for y in range(height)
    ]
    encoded = encode_tiled_image(image)
    assert len(encoded) == BACKING_WIDTH * height // 2
    assert decode_tiled_image(encoded) == image


def test_visible_conversion_pads_and_crops_at_explicit_offset():
    visible = pixels(width=228, height=80)
    visible[0][0] = 3
    visible[-1][-1] = 15

    backing = visible_to_backing(visible, x_offset=14, fill=2)
    assert backing[0][:14] == [2] * 14
    assert backing[0][14] == 3
    assert backing[-1][14 + 227] == 15
    assert backing[0][14 + 228:] == [2] * 14
    assert backing_to_visible(backing, x_offset=14) == visible

    wire = encode_visible_image(visible, x_offset=14, fill=2)
    assert len(wire) == 10240
    assert decode_visible_image(wire, x_offset=14) == visible


def test_invalid_image_shapes_and_palette_values_are_rejected():
    with pytest.raises(CodecError, match="height"):
        encode_tiled_image(pixels(height=8))
    with pytest.raises(CodecError, match="256"):
        encode_tiled_image(pixels(width=255))
    image = pixels()
    image[2][3] = 16
    with pytest.raises(CodecError, match="palette index"):
        encode_tiled_image(image)
    with pytest.raises(CodecError, match="2048"):
        decode_tiled_image(bytes(2047))


def test_drawing_record_preserves_unknown_header_regions():
    mac = bytes.fromhex("001122334455")
    image = pixels(height=32)
    image[9][17] = 7
    record = DrawingRecord.from_pixels(
        mac,
        image,
        metadata=bytes(range(14)),
        safezone=bytes(range(14, 28)),
    )
    wire = record.to_bytes()

    assert len(wire) == 36 + 4096
    assert wire[:8] == bytes.fromhex("0302110033225544")
    assert wire[8:22] == bytes(range(14))
    assert wire[22:36] == bytes(range(14, 28))
    decoded = DrawingRecord.from_bytes(wire)
    assert decoded == record
    assert decoded.row_count == 2
    assert decoded.height == 32
    assert decoded.pixels() == image


def test_drawing_record_visible_constructor_uses_documented_default_metadata():
    visible = pixels(width=228, height=16)
    visible[0][0] = 1
    record = DrawingRecord.from_visible(bytes(6), visible)
    assert record.metadata == CAPTURED_DRAWING_METADATA
    assert record.visible_pixels() == visible


def test_type1_announcement_exact_layout_and_unknown_preservation():
    trailer = bytes.fromhex("0000582b0003dba2faea")
    announcement = TransferAnnouncement(
        sender_id=3,
        data_type=2,
        data_size=0x1234,
        trailer=trailer,
    )
    wire = announcement.to_bytes()

    assert wire == (
        struct.pack("<HHBB2sH", 1, 20, 3, 2, b"\xff\xff", 0x1234)
        + trailer
    )
    assert TransferAnnouncement.from_bytes(wire) == announcement


def test_type1_accepts_client_type_zero_but_rejects_wrong_sizes():
    announcement = TransferAnnouncement(
        sender_id=1, data_type=0, data_size=84,
        trailer=bytes(10), type_id=0,
    )
    assert TransferAnnouncement.from_bytes(announcement.to_bytes()) == announcement
    malformed = bytearray(announcement.to_bytes())
    malformed[2:4] = struct.pack("<H", 19)
    with pytest.raises(CodecError, match="declares 19"):
        TransferAnnouncement.from_bytes(malformed)


def test_type2_fragment_exact_layout_and_round_trip():
    fragment = TransferFragment(
        sending_console_id=4,
        payload_type=0x97,
        transfer_flags=0,
        write_offset=180,
        magic=b"\xaa\x55",
        payload=b"abc",
    )
    wire = fragment.to_bytes()
    assert wire == struct.pack(
        "<HHBBBBH2s3s", 2, 15, 4, 0x97, 3, 0, 180, b"\xaa\x55", b"abc"
    )
    assert TransferFragment.from_bytes(wire) == fragment


def test_fragment_payload_matches_configurable_reference_policy():
    chunks = fragment_payload(
        bytes(range(200)) * 2,
        sending_console_id=2,
        chunk_size=180,
    )
    assert [part.write_offset for part in chunks] == [0, 180, 360]
    assert [len(part.payload) for part in chunks] == [180, 180, 40]
    assert [part.payload_type for part in chunks] == [0xFF, 0x97, 0x04]
    assert [part.transfer_flags for part in chunks] == [0, 0, 1]


def test_reassembly_accepts_out_of_order_and_deduplicates_retransmits():
    payload = bytes(range(251)) * 3
    announcement, chunks = build_transfer(
        payload,
        sender_id=5,
        data_type=2,
        announcement_trailer=bytes(range(10)),
        chunk_size=180,
    )
    reassembler = TransferReassembler.from_announcement(announcement)

    assert reassembler.add(chunks[-1]) is True
    assert reassembler.add(chunks[-1]) is False
    for chunk in reversed(chunks[:-1]):
        assert reassembler.add(chunk) is True

    assert reassembler.complete
    assert reassembler.duplicate_count == 1
    assert reassembler.assemble() == payload
    assert reassemble_transfer(announcement, reversed(chunks)) == payload


def test_reassembly_rejects_conflicts_overflow_and_bad_final_position():
    reassembler = TransferReassembler(expected_size=4, sending_console_id=1)
    first = TransferFragment(1, 0xFF, 0, 0, b"ab")
    reassembler.add(first)
    with pytest.raises(CodecError, match="conflicting"):
        reassembler.add(TransferFragment(1, 0x97, 0, 1, b"Z"))
    with pytest.raises(CodecError, match="exceeds"):
        reassembler.add(TransferFragment(1, 0x97, 0, 3, b"xy"))
    with pytest.raises(CodecError, match="final fragment"):
        reassembler.add(TransferFragment(1, 0x04, 1, 2, b"x"))
    with pytest.raises(CodecError, match="incomplete"):
        reassembler.assemble()


def test_reassembly_requires_final_marker_even_when_all_bytes_exist():
    reassembler = TransferReassembler(expected_size=3, sending_console_id=0)
    reassembler.add(TransferFragment(0, 0xFF, 0, 0, b"abc"))
    assert reassembler.has_all_bytes
    assert not reassembler.complete
    with pytest.raises(CodecError, match="no final"):
        reassembler.assemble()
    assert reassembler.assemble(allow_without_final=True) == b"abc"


def test_zero_byte_transfer_completes_on_one_empty_final_fragment():
    announcement, chunks = build_transfer(
        b"",
        sender_id=0,
        data_type=0,
        announcement_trailer=bytes(10),
    )
    assert len(chunks) == 1
    reassembler = TransferReassembler.from_announcement(announcement)
    assert reassembler.add(chunks[0]) is True
    assert reassembler.complete
    assert reassembler.assemble() == b""


def test_malformed_type2_length_is_rejected():
    wire = bytearray(TransferFragment(0, 4, 1, 0, b"abc").to_bytes())
    wire[6] = 4  # declared payload length, actual payload is three bytes
    with pytest.raises(CodecError, match="payload declares 4"):
        TransferFragment.from_bytes(wire)
