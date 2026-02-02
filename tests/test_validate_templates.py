"""Test that template attribute references are valid."""

import sys
from pathlib import Path

# Add scripts directory to path
scripts_dir = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(scripts_dir))

from validate_templates import validate_templates


def test_template_attribute_references():
    """Validate that all template attribute references exist in SQLAlchemy models."""
    result = validate_templates()
    assert result == 0, "Template validation failed - invalid attribute references found"
