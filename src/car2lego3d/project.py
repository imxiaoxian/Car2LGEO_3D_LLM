"""Deployment identity and explicit reconstruction routing for the unified project."""

PROJECT_NAME = "Car2LGEO_3D_LLM"
DEFAULT_BACKEND = "gpt-blender"
DESKTOP_BACKENDS = ("gpt-blender", "hunyuan-mv", "hybrid")
FRONTEND_PORT = 8778
API_PORT = 8777
LEGACY_WEB_PORT = 8775


def desktop_backend(requested=None):
    if requested and requested not in DESKTOP_BACKENDS:
        raise ValueError("Unknown desktop reconstruction backend")
    return requested or DEFAULT_BACKEND
