from unittest.mock import patch
import pytest
from rag_local.cli import draw_suggestions, Theme

@pytest.fixture
def test_theme():
    return Theme("Test Theme", "primary", "secondary", "text", "accent")

def test_draw_suggestions_empty(test_theme):
    with patch("sys.stdout.write") as mock_write:
        lines = draw_suggestions([], 0, test_theme, 10, 0, 0)
        assert lines == 0
        mock_write.assert_not_called()

def test_draw_suggestions_less_than_viewport(test_theme):
    matches = [
        ("/a", "desc A"),
        ("/b", "desc B"),
        ("/c", "desc C")
    ]
    with patch("sys.stdout.write") as mock_write:
        lines = draw_suggestions(matches, 1, test_theme, 10, 2, 0)
        assert lines == 3
        # Should NOT draw scrollbar since n_matches (3) <= viewport_size (5)
        # Verify call arguments
        written_content = "".join(call[0][0] for call in mock_write.call_args_list)
        # Verify highlight (global index 1 is "/b", which is local index 1)
        assert "▶ /b" in written_content
        # Scrollbar characters should not be in the output
        assert "┃" not in written_content
        assert "│" not in written_content

def test_draw_suggestions_more_than_viewport_scrollbar(test_theme):
    matches = [
        ("/1", "desc 1"),
        ("/2", "desc 2"),
        ("/3", "desc 3"),
        ("/4", "desc 4"),
        ("/5", "desc 5"),
        ("/6", "desc 6"),
        ("/7", "desc 7"),
    ]
    # Test top viewport (view_offset = 0)
    with patch("sys.stdout.write") as mock_write:
        lines = draw_suggestions(matches, 2, test_theme, 10, 2, 0)
        assert lines == 5 # viewport height is 5
        written_content = "".join(call[0][0] for call in mock_write.call_args_list)
        # Should slice to first 5
        assert "/1" in written_content
        assert "/5" in written_content
        assert "/6" not in written_content
        
        # Verify active highlight on "/3" (global index 2)
        assert "▶ /3" in written_content
        
        # Should have scrollbar characters (thumb and track)
        assert "┃" in written_content or "│" in written_content

def test_draw_suggestions_scrolled_viewport(test_theme):
    matches = [
        ("/1", "desc 1"),
        ("/2", "desc 2"),
        ("/3", "desc 3"),
        ("/4", "desc 4"),
        ("/5", "desc 5"),
        ("/6", "desc 6"),
        ("/7", "desc 7"),
    ]
    # Test scrolled viewport (view_offset = 2)
    with patch("sys.stdout.write") as mock_write:
        lines = draw_suggestions(matches, 5, test_theme, 10, 2, 2)
        assert lines == 5
        written_content = "".join(call[0][0] for call in mock_write.call_args_list)
        # Should slice to index 2..6, which is "/3".."/7"
        assert "/1" not in written_content
        assert "/2" not in written_content
        assert "/3" in written_content
        assert "/7" in written_content
        
        # Verify active highlight on "/6" (global index 5, local index 3)
        assert "▶ /6" in written_content
