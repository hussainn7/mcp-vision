"""Phase 1 - Vision Pipeline"""

from .app_detector import get_frontmost_app, get_display_info, is_browser_app
from .accessibility import get_accessibility_elements
from .perception import (
    AccessibilityPermissionError,
    get_perception_data,
    get_perception_method_name,
    is_perception_available,
)
