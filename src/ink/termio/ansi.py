"""ANSI Control Characters and Escape Sequence Introducers.

Based on ECMA-48 / ANSI X3.64 standards.
"""


class C0:
    """C0 (7-bit) control characters."""
    NUL = 0x00
    SOH = 0x01
    STX = 0x02
    ETX = 0x03
    EOT = 0x04
    ENQ = 0x05
    ACK = 0x06
    BEL = 0x07
    BS = 0x08
    HT = 0x09
    LF = 0x0A
    VT = 0x0B
    FF = 0x0C
    CR = 0x0D
    SO = 0x0E
    SI = 0x0F
    DLE = 0x10
    DC1 = 0x11
    DC2 = 0x12
    DC3 = 0x13
    DC4 = 0x14
    NAK = 0x15
    SYN = 0x16
    ETB = 0x17
    CAN = 0x18
    EM = 0x19
    SUB = 0x1A
    ESC = 0x1B
    FS = 0x1C
    GS = 0x1D
    RS = 0x1E
    US = 0x1F
    DEL = 0x7F


# String constants for output generation
ESC = "\x1b"
BEL = "\x07"
SEP = ";"


class ESC_TYPE:
    """Escape sequence type introducers (byte after ESC)."""
    CSI = 0x5B   # [ - Control Sequence Introducer
    OSC = 0x5D   # ] - Operating System Command
    DCS = 0x50   # P - Device Control String
    APC = 0x5F   # _ - Application Program Command
    PM = 0x5E    # ^ - Privacy Message
    SOS = 0x58   # X - Start of String
    ST = 0x5C    # \ - String Terminator


def is_c0(byte: int) -> bool:
    """Check if a byte is a C0 control character."""
    return byte < 0x20 or byte == 0x7F


def is_esc_final(byte: int) -> bool:
    """Check if a byte is an ESC sequence final byte (0-9, :, ;, <, =, >, ?, @ through ~)."""
    return 0x30 <= byte <= 0x7E
