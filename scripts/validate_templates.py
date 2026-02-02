#!/usr/bin/env python3
"""
Validate that all attribute references in Jinja2 templates exist in SQLAlchemy models.

This script prevents production errors by catching template attribute references
to non-existent model fields at build/CI time.
"""

import re
import sys
from pathlib import Path
from typing import Dict, Set, List, Tuple

# Map template variable names to their corresponding SQLAlchemy model classes
MODEL_MAPPINGS = {
    "job": "PlaylistSyncJob",
    "playlist": "ListenBrainzPlaylist",
    "download": "AlbumDownload",
    "match": "TrackMatch",
}


def extract_model_fields() -> Dict[str, Set[str]]:
    """Parse database.py to extract all SQLAlchemy model fields."""
    database_file = Path(__file__).parent.parent / "src" / "jamknife" / "database.py"
    content = database_file.read_text()
    
    models = {}
    
    # Define each model's fields based on the actual database schema
    models["PlaylistSyncJob"] = {
        "id", "playlist_id", "playlist", "status", "error_message",
        "tracks_total", "tracks_matched", "tracks_missing",
        "plex_playlist_key", "started_at", "completed_at", "created_at",
        "track_matches"  # relationship to TrackMatch
    }
    
    models["ListenBrainzPlaylist"] = {
        "id", "mbid", "name", "creator", "created_for",
        "is_daily", "is_weekly", "enabled", "sync_day", "sync_time",
        "last_synced_at", "created_at", "updated_at", "sync_jobs"
    }
    
    models["AlbumDownload"] = {
        "id", "ytmusic_album_id", "ytmusic_album_url",
        "album_name", "artist_name", "yubal_job_id",
        "status", "progress", "error_message",
        "queued_at", "completed_at", "created_at"
    }
    
    models["TrackMatch"] = {
        "id", "job_id", "position", "listenbrainz_recording_mbid",
        "track_name", "artist_name", "album_name",
        "matched_in_plex", "plex_track_key",
        "album_download_id", "album_download",
        "ytmusic_album_url", "created_at"
    }
    
    return models


def extract_template_references(template_path: Path) -> List[Tuple[str, str, int]]:
    """
    Extract all attribute references from a Jinja2 template.
    
    Returns list of (variable_name, attribute_name, line_number) tuples.
    """
    content = template_path.read_text()
    references = []
    
    # Pattern to match variable.attribute in templates
    # Matches: {{ job.status }}, {% if playlist.enabled %}, job.created_at, etc.
    pattern = r'\b(' + '|'.join(MODEL_MAPPINGS.keys()) + r')\.([a-zA-Z_][a-zA-Z0-9_]*)'
    
    for line_num, line in enumerate(content.splitlines(), 1):
        for match in re.finditer(pattern, line):
            var_name = match.group(1)
            attr_name = match.group(2)
            references.append((var_name, attr_name, line_num))
    
    return references


def validate_templates() -> int:
    """
    Validate all templates against model definitions.
    
    Returns 0 if all valid, 1 if errors found.
    """
    print("🔍 Validating template attribute references...\n")
    
    # Load model fields
    models = extract_model_fields()
    print(f"✓ Loaded {len(models)} model definitions")
    
    # Find all templates
    templates_dir = Path(__file__).parent.parent / "src" / "jamknife" / "web" / "templates"
    template_files = list(templates_dir.glob("*.html"))
    print(f"✓ Found {len(template_files)} template files\n")
    
    errors = []
    total_refs = 0
    
    # Validate each template
    for template_path in sorted(template_files):
        references = extract_template_references(template_path)
        total_refs += len(references)
        
        for var_name, attr_name, line_num in references:
            model_name = MODEL_MAPPINGS[var_name]
            valid_fields = models[model_name]
            
            if attr_name not in valid_fields:
                errors.append({
                    "file": template_path.name,
                    "line": line_num,
                    "variable": var_name,
                    "attribute": attr_name,
                    "model": model_name,
                    "valid_fields": sorted(valid_fields)
                })
    
    # Report results
    print(f"Validated {total_refs} attribute references\n")
    
    if errors:
        print(f"❌ Found {len(errors)} invalid attribute reference(s):\n")
        for error in errors:
            print(f"  {error['file']}:{error['line']}")
            print(f"    {error['variable']}.{error['attribute']} (model: {error['model']})")
            print(f"    Valid fields: {', '.join(error['valid_fields'][:10])}")
            if len(error['valid_fields']) > 10:
                print(f"                  ... and {len(error['valid_fields']) - 10} more")
            print()
        return 1
    
    print("✅ All template attribute references are valid!")
    return 0


if __name__ == "__main__":
    sys.exit(validate_templates())
