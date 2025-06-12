import pytest
from backend.app.mavlink_parser import MAVLinkParser
from pathlib import Path

# This test checks that the parser can be instantiated and handles missing files gracefully.
def test_mavlink_parser_instantiation():
    parser = MAVLinkParser(Path("nonexistent.tlog"))
    with pytest.raises(Exception):
        parser.parse() 