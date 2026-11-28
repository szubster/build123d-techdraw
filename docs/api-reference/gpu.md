# GPU Acceleration API

Optional WebGPU (`wgpu`) runtime detection, orthographic projection cameras, B-Rep tessellation, technical-illustration shading, visibility-buffer HLR, and 2D distance-field (SDF) layout.

## Runtime Detection & Device Management

::: build123d_techdraw.gpu.runtime
    options:
      members:
        - GPU_ENV_VAR
        - is_gpu_installed
        - is_gpu_available
        - is_gpu_enabled
        - get_gpu_device
        - gpu_info
        - reset_gpu_cache

## Tessellation & Edge Sampling

::: build123d_techdraw.gpu.mesh
    options:
      members:
        - TessellatedMesh
        - SampledEdge
        - SampledEdgeSet
        - tessellate_shape
        - sample_shape_edges

## Orthographic Camera

::: build123d_techdraw.gpu.camera
    options:
      members:
        - OrthoCamera
        - compute_ortho_camera
        - compute_ortho_camera_for_points

## Technical Shading

::: build123d_techdraw.gpu.shading
    options:
      members:
        - ViewStyle
        - ShadedRender
        - ENGINEERING_BOM_PALETTE
        - render_shaded_view

## Visibility-Buffer HLR

::: build123d_techdraw.gpu.hlr
    options:
      members:
        - HLRMode
        - HLROptions
        - GPUHLRStats
        - GPUHLRResult
        - count_shape_faces
        - resolve_hlr_backend
        - project_shape_gpu_hlr

## Distance-Field (SDF / JFA)

::: build123d_techdraw.gpu.sdf
    options:
      members:
        - SheetDistanceField
        - compute_distance_field_from_mask
        - compute_sheet_sdf
