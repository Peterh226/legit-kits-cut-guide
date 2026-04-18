"""
Land of the Free — Block Assembly Data
========================================
Derived from the Legit Kits Assembly Guide PDF.

BLOCKS maps each block ID to the list of its fragment IDs.
  - Single-fragment blocks:  one entry equal to the block ID  (e.g. "A1": ["A1"])
  - Multi-fragment blocks:   fragments listed in assembly order (e.g. "A4": ["A4a","A4b"])
"""


def _frags(block, letters):
    """Build fragment IDs from a block ID and a string of letter suffixes."""
    return [f"{block}{c}" for c in letters]


BLOCKS = {
    # ── Row A ──────────────────────────────────────────────────────────────────
    "A1": ["A1"],
    "A2": ["A2"],
    "A3": ["A3"],
    "A4": _frags("A4", "ab"),
    "A5": ["A5"],
    "A6": ["A6"],
    "A7": ["A7"],
    "A8": _frags("A8", "ab"),

    # ── Row B ──────────────────────────────────────────────────────────────────
    "B1": ["B1"],
    "B2": ["B2"],
    "B3": _frags("B3", "ab"),
    "B4": ["B4"],
    "B5": ["B5"],
    "B6": _frags("B6", "abc"),
    "B7": _frags("B7", "abcdefgh"),
    "B8": _frags("B8", "abcdef"),

    # ── Row C ──────────────────────────────────────────────────────────────────
    "C1": ["C1"],
    "C2": ["C2"],
    "C3": _frags("C3", "ab"),
    "C4": ["C4"],
    "C5": ["C5"],
    "C6": _frags("C6", "abcde"),
    "C7": _frags("C7", "abcdef"),
    "C8": _frags("C8", "abcdef"),

    # ── Row D ──────────────────────────────────────────────────────────────────
    "D1": ["D1"],
    "D2": _frags("D2", "abcd"),
    "D3": ["D3"],
    "D4": ["D4"],
    "D5": _frags("D5", "abcdefghi"),
    "D6": _frags("D6", "ab"),
    "D7": _frags("D7", "abcdefg"),
    "D8": _frags("D8", "abcdefg"),

    # ── Row E ──────────────────────────────────────────────────────────────────
    "E1": _frags("E1", "abcdefghijk"),
    "E2": _frags("E2", "abcdefghijklm"),
    "E3": _frags("E3", "abcdefghi"),
    "E4": _frags("E4", "abcdefghijklmnopqrs"),
    "E5": _frags("E5", "abcdefghijklmno"),
    "E6": _frags("E6", "abcdefg"),
    "E7": _frags("E7", "abcdefghijk"),
    "E8": _frags("E8", "ab"),

    # ── Row F ──────────────────────────────────────────────────────────────────
    "F1": _frags("F1", "abcdefghijklm"),
    "F2": _frags("F2", "abcde"),
    "F3": _frags("F3", "abcdefghijklmnopqrstuv"),
    "F4": _frags("F4", "abcdefghijklmnopqrstuvw"),
    "F5": _frags("F5", "abcdefghi"),
    "F6": _frags("F6", "abcdef"),
    "F7": _frags("F7", "abcdefg"),
    "F8": _frags("F8", "abcdefghijklmnop"),

    # ── Row G ──────────────────────────────────────────────────────────────────
    "G1": _frags("G1", "abcdefg"),
    "G2": _frags("G2", "abcdefg"),
    "G3": _frags("G3", "ab"),
    "G4": ["G4"],
    "G5": ["G5"],
    "G6": _frags("G6", "abcd"),
    "G7": _frags("G7", "abcdefghijklmnopqrstuvwx"),
    "G8": _frags("G8", "abcdefghij"),

    # ── Row H ──────────────────────────────────────────────────────────────────
    "H1": _frags("H1", "abcde"),
    "H2": ["H2"],
    "H3": ["H3"],
    "H4": ["H4"],
    "H5": ["H5"],
    "H6": ["H6"],
    "H7": ["H7"],
    "H8": ["H8"],
}
