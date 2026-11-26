"""Optional WebGPU (wgpu) foundation for hardware-accelerated technical drawing."""

from build123d_techdraw.gpu.camera import (
    OrthoCamera,
    compute_ortho_camera,
    compute_ortho_camera_for_points,
)
from build123d_techdraw.gpu.hlr import (
    GPUHLRResult,
    GPUHLRStats,
    HLRMode,
    HLROptions,
    count_shape_faces,
    project_shape_gpu_hlr,
    resolve_hlr_backend,
)
from build123d_techdraw.gpu.mesh import (
    SampledEdge,
    SampledEdgeSet,
    TessellatedMesh,
    sample_shape_edges,
    tessellate_shape,
)
from build123d_techdraw.gpu.runtime import (
    GPU_ENV_VAR,
    get_gpu_device,
    gpu_info,
    is_gpu_available,
    is_gpu_enabled,
    is_gpu_installed,
    reset_gpu_cache,
)
from build123d_techdraw.gpu.sdf import (
    SheetDistanceField,
    compute_distance_field_from_mask,
    compute_sheet_sdf,
)
from build123d_techdraw.gpu.shading import (
    ENGINEERING_BOM_PALETTE,
    ShadedRender,
    ViewStyle,
    render_shaded_view,
)

__all__ = [
    "ENGINEERING_BOM_PALETTE",
    "GPUHLRResult",
    "GPUHLRStats",
    "GPU_ENV_VAR",
    "HLRMode",
    "HLROptions",
    "OrthoCamera",
    "SampledEdge",
    "SampledEdgeSet",
    "ShadedRender",
    "SheetDistanceField",
    "TessellatedMesh",
    "ViewStyle",
    "compute_distance_field_from_mask",
    "compute_ortho_camera",
    "compute_ortho_camera_for_points",
    "compute_sheet_sdf",
    "count_shape_faces",
    "get_gpu_device",
    "gpu_info",
    "is_gpu_available",
    "is_gpu_enabled",
    "is_gpu_installed",
    "project_shape_gpu_hlr",
    "render_shaded_view",
    "reset_gpu_cache",
    "resolve_hlr_backend",
    "sample_shape_edges",
    "tessellate_shape",
]
