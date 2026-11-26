"""WGSL technical-illustration shading for raster layers drawn under exact vector edges.

A shaded view is rendered offscreen with ``wgpu`` in three passes:

1. **Geometry G-buffer**: the tessellated B-Rep is rasterized orthographically with the
   :class:`~build123d_techdraw.gpu.camera.OrthoCamera` basis used by ``build123d.Drawing``,
   writing per-part albedo (``rgba8unorm``), view-space normals (``rgba16float``) and
   ``depth32float``. X-ray views instead accumulate Fresnel-weighted coverage of *every*
   surface layer with additive blending, so internal bores show through.
2. **Screen-space ambient occlusion**: a hemisphere-kernel pass over depth + normals darkens
   bores, fillets, counterbores and contact seams so 3D form reads on a 2D sheet.
3. **Composite**: one style-specific lighting pass per theme (Gooch, toon, blueprint, ...).

The frame is supersampled and box-filtered (premultiplied alpha) into a transparent RGBA
:class:`PIL.Image.Image`. :meth:`ShadedRender.to_svg` places it in the view's local
``build123d.Drawing`` coordinates, so it can be drawn *before* ``#layer_visible`` and the exact
vector outlines stay sharp on top. ``wgpu`` is imported lazily: this module stays importable
without the ``gpu`` extra.
"""

from __future__ import annotations

import base64
import io
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, TypedDict, Unpack, runtime_checkable

import numpy as np
from build123d.topology import Shape
from numpy.typing import NDArray
from PIL import Image, ImageColor

from build123d_techdraw.gpu.camera import OrthoCamera, compute_ortho_camera_for_points
from build123d_techdraw.gpu.mesh import TessellatedMesh, tessellate_shape
from build123d_techdraw.gpu.runtime import get_gpu_device, is_gpu_enabled

if TYPE_CHECKING:
    from build123d_techdraw.renderers.theme import Theme

Vec3 = tuple[float, float, float]
ColorSpec = str | tuple[float, float, float]
PartColors = Sequence[ColorSpec] | Mapping[str, ColorSpec]


@runtime_checkable
class _HasCenter(Protocol):
    def center(self) -> Any: ...


class ViewStyle(Enum):
    """How a projected view is drawn: exact vectors only, or a GPU-shaded raster layer."""

    WIREFRAME = "wireframe"
    """Classic exact vector drawing (default, no GPU work)."""
    SHADED = "shaded"
    """Smooth technical shading without a vector edge overlay."""
    SHADED_WITH_EDGES = "shaded_with_edges"
    """Smooth technical shading underneath the exact vector edges."""
    GOOCH = "gooch"
    """Gooch cool-to-warm technical illustration shading + subtle SSAO + vector edges."""
    TOON = "toon"
    """Quantized cel bands + vector edges."""
    BLUEPRINT_SHADED = "blueprint_shaded"
    """Holographic blueprint shading (cyan in dark theme, gray-blue in white) + vector edges."""
    XRAY = "xray"
    """Fresnel translucent shading revealing internal features + vector edges."""

    @classmethod
    def coerce(cls, value: ViewStyle | str) -> ViewStyle:
        """Return ``value`` as a :class:`ViewStyle`, accepting its case-insensitive string value."""
        if isinstance(value, ViewStyle):
            return value
        try:
            return cls(value.strip().lower())
        except ValueError as exc:
            choices = ", ".join(repr(s.value) for s in cls)
            raise ValueError(f"Unknown view style {value!r}; expected one of {choices}.") from exc


class ShadingKwargs(TypedDict, total=False):
    """Optional shading keywords accepted by view factories (see :func:`render_shaded_view`)."""

    style: ViewStyle | str
    shading_dpi: int
    color_by_part: bool
    part_colors: PartColors | None
    ssao: bool
    gpu: bool | None
    shading_theme: Theme | str | None


# Harmonious engineering-material BOM palette, readable on both the dark and white sheets.
ENGINEERING_BOM_PALETTE: tuple[str, ...] = (
    "#8A9BB0",  # machined steel
    "#4C8DD6",  # blue anodized aluminium
    "#D4A24C",  # brass
    "#C0704F",  # bronze
    "#6FAE8B",  # green-coated steel / nylon
    "#A58BD0",  # violet anodized aluminium
    "#D7D9D2",  # raw aluminium
    "#E07A7A",  # red anodized aluminium
)

_STYLE_IDS: dict[ViewStyle, int] = {
    ViewStyle.SHADED: 1,
    ViewStyle.SHADED_WITH_EDGES: 1,
    ViewStyle.GOOCH: 2,
    ViewStyle.TOON: 3,
    ViewStyle.BLUEPRINT_SHADED: 4,
    ViewStyle.XRAY: 5,
}

_SUPERSAMPLE = 2
_SUPERSAMPLE_MAX_PIXELS = 4_000_000
_PADDING_PX = 2.0
_SSAO_SAMPLES = 16
_MM_PER_INCH = 25.4

# Key light from the upper left (technical illustration convention), soft fill from the right.
_KEY_LIGHT: Vec3 = (-0.45, 0.62, 0.64)
_FILL_LIGHT: Vec3 = (0.7, -0.15, 0.7)


@dataclass(frozen=True)
class _StylePalette:
    """Theme-specific colours fed to the composite shader (linear 0..1 RGB)."""

    base: Vec3
    cool: Vec3
    warm: Vec3
    rim: Vec3
    ambient: float
    fill: float
    specular: float
    opacity: float
    ao_strength: float
    rim_strength: float = 0.0
    part_tint: float = 0.0
    xray_gain: float = 0.0


def _palette(style: ViewStyle, theme: Theme) -> _StylePalette:
    """Colours per style and theme: mid-tones under white edges, light tones under black."""
    dark = theme.value == "dark_blueprint"
    if style is ViewStyle.GOOCH:
        if dark:
            return _StylePalette(
                base=(0.42, 0.50, 0.62),
                cool=(0.02, 0.10, 0.30),
                warm=(0.42, 0.34, 0.16),
                rim=(1.0, 1.0, 1.0),
                ambient=0.0,
                fill=0.0,
                specular=0.18,
                opacity=1.0,
                ao_strength=0.55,
            )
        return _StylePalette(
            base=(0.78, 0.80, 0.84),
            cool=(0.0, 0.06, 0.32),
            warm=(0.36, 0.32, 0.0),
            rim=(1.0, 1.0, 1.0),
            ambient=0.0,
            fill=0.0,
            specular=0.22,
            opacity=1.0,
            ao_strength=0.45,
        )
    if style is ViewStyle.BLUEPRINT_SHADED:
        if dark:
            return _StylePalette(
                base=(0.22, 0.74, 0.97),
                cool=(0.02, 0.13, 0.27),
                warm=(0.20, 0.62, 0.86),
                rim=(0.55, 0.90, 1.0),
                ambient=0.0,
                fill=0.0,
                specular=0.12,
                opacity=0.86,
                ao_strength=0.8,
                rim_strength=0.35,
                part_tint=0.45,
            )
        return _StylePalette(
            base=(0.55, 0.64, 0.75),
            cool=(0.50, 0.59, 0.70),
            warm=(0.90, 0.93, 0.97),
            rim=(0.30, 0.40, 0.55),
            ambient=0.0,
            fill=0.0,
            specular=0.10,
            opacity=1.0,
            ao_strength=0.8,
            rim_strength=-0.25,
            part_tint=0.45,
        )
    if style is ViewStyle.XRAY:
        if dark:
            return _StylePalette(
                base=(0.40, 0.82, 1.0),
                cool=(0.0, 0.0, 0.0),
                warm=(0.0, 0.0, 0.0),
                rim=(0.92, 0.98, 1.0),
                ambient=0.0,
                fill=0.0,
                specular=0.0,
                opacity=0.95,
                ao_strength=0.0,
                xray_gain=2.2,
            )
        return _StylePalette(
            base=(0.16, 0.36, 0.62),
            cool=(0.0, 0.0, 0.0),
            warm=(0.0, 0.0, 0.0),
            rim=(0.04, 0.12, 0.26),
            ambient=0.0,
            fill=0.0,
            specular=0.0,
            opacity=0.9,
            ao_strength=0.0,
            xray_gain=2.2,
        )
    # Smooth shading and toon share the neutral machined-metal base.
    base = (0.40, 0.49, 0.60) if dark else (0.80, 0.83, 0.87)
    return _StylePalette(
        base=base,
        cool=(0.0, 0.0, 0.0),
        warm=(0.0, 0.0, 0.0),
        rim=(1.0, 1.0, 1.0),
        ambient=0.38 if dark else 0.42,
        fill=0.18,
        specular=0.0 if style is ViewStyle.TOON else (0.22 if dark else 0.30),
        opacity=1.0,
        ao_strength=0.85,
    )


def _style_uniform(style: ViewStyle, theme: Theme, ssao: bool) -> NDArray[np.float32]:
    """Pack the WGSL ``Style`` uniform (7 x vec4<f32>)."""
    p = _palette(style, theme)
    return np.array(
        [
            [*p.base, float(_STYLE_IDS[style])],
            [*p.cool, p.ao_strength],
            [*p.warm, p.opacity],
            [*p.rim, p.specular],
            [*_KEY_LIGHT, p.ambient],
            [*_FILL_LIGHT, p.fill],
            [p.part_tint, p.rim_strength, 1.0 if ssao else 0.0, p.xray_gain],
        ],
        dtype=np.float32,
    )


_FULLSCREEN_WGSL = """
@vertex
fn vs_fullscreen(@builtin(vertex_index) vi: u32) -> @builtin(position) vec4<f32> {
    let x = f32((vi << 1u) & 2u) * 2.0 - 1.0;
    let y = f32(vi & 2u) * 2.0 - 1.0;
    return vec4<f32>(x, y, 0.0, 1.0);
}
"""

_STYLE_STRUCT_WGSL = """
struct Style {
    base: vec4<f32>,   // rgb default albedo, w style id
    cool: vec4<f32>,   // rgb cool / shadow tint, w ambient-occlusion strength
    warm: vec4<f32>,   // rgb warm / glow tint, w opacity
    rim: vec4<f32>,    // rgb rim / density tint, w specular strength
    key: vec4<f32>,    // xyz key light (view space), w ambient
    fill: vec4<f32>,   // xyz fill light (view space), w fill strength
    extra: vec4<f32>,  // x part tint weight, y rim strength, z ssao enabled, w x-ray gain
};
@group(0) @binding(0) var<uniform> style: Style;
"""

_MESH_WGSL = """
struct Scene {
    view_proj: mat4x4<f32>,
    view: mat4x4<f32>,
    xray_tint: vec4<f32>,
};
@group(0) @binding(0) var<uniform> scene: Scene;

struct VsIn {
    @location(0) position: vec3<f32>,
    @location(1) normal: vec3<f32>,
    @location(2) color: vec4<f32>,
};

struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) normal_view: vec3<f32>,
    @location(1) color: vec4<f32>,
    @location(2) eye_dist: f32,
};

@vertex
fn vs_mesh(in: VsIn) -> VsOut {
    var out: VsOut;
    let pos_view = scene.view * vec4<f32>(in.position, 1.0);
    out.clip = scene.view_proj * vec4<f32>(in.position, 1.0);
    out.normal_view = (scene.view * vec4<f32>(in.normal, 0.0)).xyz;
    out.color = in.color;
    out.eye_dist = -pos_view.z;
    return out;
}

struct GBuffer {
    @location(0) albedo: vec4<f32>,
    @location(1) normal_depth: vec4<f32>,
};

@fragment
fn fs_gbuffer(in: VsOut, @builtin(front_facing) front: bool) -> GBuffer {
    var out: GBuffer;
    let n = normalize(in.normal_view);
    out.albedo = in.color;
    // Store 1.0 + clip.z (in [1, 2]) so rgba16float has uniform 10-bit depth precision and 0 marks background.
    out.normal_depth = vec4<f32>(select(-n, n, front), 1.0 + clamp(in.clip.z, 0.0, 1.0));
    return out;
}

@fragment
fn fs_xray(in: VsOut) -> @location(0) vec4<f32> {
    let n = normalize(in.normal_view);
    let w = 0.03 + 0.97 * pow(1.0 - abs(n.z), 2.5);
    let tint = select(scene.xray_tint.rgb, in.color.rgb, in.color.a > 0.75);
    return vec4<f32>(tint * w, w);
}
"""

_SSAO_WGSL = (
    _FULLSCREEN_WGSL
    + """
struct Ssao {
    frame: vec4<f32>,  // half width mm, half height mm, mm per pixel, radius mm
    depth: vec4<f32>,  // z near, z far, bias mm, intensity
    kernel: array<vec4<f32>, 16>,
};
@group(0) @binding(0) var<uniform> ssao: Ssao;
@group(0) @binding(1) var normal_tex: texture_2d<f32>;

fn eye_distance(nd_w: f32) -> f32 {
    return ssao.depth.x + (nd_w - 1.0) * (ssao.depth.y - ssao.depth.x);
}

@fragment
fn fs_ssao(@builtin(position) frag: vec4<f32>) -> @location(0) vec4<f32> {
    let px = vec2<i32>(frag.xy);
    let nd = textureLoad(normal_tex, px, 0);
    if (nd.w < 0.5) {
        return vec4<f32>(1.0);
    }
    let dims = vec2<i32>(textureDimensions(normal_tex));
    let c = (vec2<f32>(px) + vec2<f32>(0.5)) * ssao.frame.z;
    let p = vec3<f32>(c.x - ssao.frame.x, ssao.frame.y - c.y, -eye_distance(nd.w));
    let n = normalize(nd.xyz);

    // 4x4 tiled kernel rotation; the composite pass blurs exactly one tile.
    let cell = vec2<f32>(f32(px.x & 3), f32(px.y & 3));
    let angle = 6.2831853 * fract(52.9829189 * fract(dot(cell, vec2<f32>(0.06711056, 0.00583715))));
    var t = vec3<f32>(cos(angle), sin(angle), 0.0);
    t = t - n * dot(t, n);
    if (dot(t, t) < 1e-6) {
        t = vec3<f32>(0.0, 0.0, 1.0) - n * n.z;
    }
    t = normalize(t);
    let b = cross(n, t);

    var occlusion = 0.0;
    for (var i = 0u; i < 16u; i = i + 1u) {
        let k = ssao.kernel[i].xyz;
        let s = p + (t * k.x + b * k.y + n * k.z) * ssao.frame.w;
        let sp = vec2<f32>(s.x + ssao.frame.x, ssao.frame.y - s.y) / ssao.frame.z;
        let spx = vec2<i32>(floor(sp));
        if (any(spx < vec2<i32>(0)) || any(spx >= dims)) {
            continue;
        }
        let sample_nd = textureLoad(normal_tex, spx, 0);
        if (sample_nd.w < 0.5) {
            continue;
        }
        let scene_z = -eye_distance(sample_nd.w);
        let range = smoothstep(0.0, 1.0, ssao.frame.w / max(abs(p.z - scene_z), 1e-4));
        if (scene_z >= s.z + ssao.depth.z) {
            occlusion = occlusion + range;
        }
    }
    let ao = clamp(1.0 - ssao.depth.w * occlusion / 16.0, 0.0, 1.0);
    return vec4<f32>(ao, ao, ao, 1.0);
}
"""
)

_COMPOSITE_WGSL = (
    _FULLSCREEN_WGSL
    + _STYLE_STRUCT_WGSL
    + """
@group(0) @binding(1) var albedo_tex: texture_2d<f32>;
@group(0) @binding(2) var normal_tex: texture_2d<f32>;
@group(0) @binding(3) var ao_tex: texture_2d<f32>;

fn smoothed_ao(px: vec2<i32>, n0: vec3<f32>, dims: vec2<i32>) -> f32 {
    var total = 0.0;
    var weight = 0.0;
    for (var dy = -2; dy < 2; dy = dy + 1) {
        for (var dx = -2; dx < 2; dx = dx + 1) {
            let q = clamp(px + vec2<i32>(dx, dy), vec2<i32>(0), dims - vec2<i32>(1));
            let nq = textureLoad(normal_tex, q, 0);
            if (nq.w <= 0.0) {
                continue;
            }
            let w = max(dot(nq.xyz, n0), 0.0) + 0.02;
            total = total + textureLoad(ao_tex, q, 0).r * w;
            weight = weight + w;
        }
    }
    return select(1.0, total / weight, weight > 0.0);
}

@fragment
fn fs_composite(@builtin(position) frag: vec4<f32>) -> @location(0) vec4<f32> {
    let px = vec2<i32>(frag.xy);
    let nd = textureLoad(normal_tex, px, 0);
    if (nd.w <= 0.0) {
        return vec4<f32>(0.0);
    }
    let dims = vec2<i32>(textureDimensions(normal_tex));
    let n = normalize(nd.xyz);
    let albedo = textureLoad(albedo_tex, px, 0);
    let part_colored = albedo.a > 0.75;
    let base = select(style.base.rgb, albedo.rgb, part_colored);

    var ao = 1.0;
    if (style.extra.z > 0.5) {
        ao = mix(1.0, smoothed_ao(px, n, dims), style.cool.w);
    }

    let l = normalize(style.key.xyz);
    let h = normalize(l + vec3<f32>(0.0, 0.0, 1.0));
    let ndl = dot(n, l);
    let diffuse = max(ndl, 0.0);
    let fill = max(dot(n, normalize(style.fill.xyz)), 0.0) * style.fill.w;
    let spec = pow(max(dot(n, h), 0.0), 48.0) * style.rim.w;
    let fresnel = pow(1.0 - clamp(n.z, 0.0, 1.0), 3.0);

    var rgb: vec3<f32>;
    var alpha = style.warm.w;
    switch u32(style.base.w + 0.5) {
        case 2u: {
            // Gooch: cool-to-warm tone ramp keeps shadows readable under dark/light edges.
            let t = 0.5 + 0.5 * ndl;
            let k_cool = style.cool.rgb + 0.25 * base;
            let k_warm = style.warm.rgb + 0.5 * base;
            rgb = mix(k_cool, k_warm, t) * ao + vec3<f32>(spec);
        }
        case 3u: {
            // Toon: quantized diffuse bands, quantized occlusion and a hard specular band.
            var band = 0.5;
            if (ndl > 0.55) {
                band = 1.0;
            } else if (ndl > 0.15) {
                band = 0.82;
            } else if (ndl > -0.25) {
                band = 0.66;
            }
            let ao_band = select(1.0, 0.8, ao < 0.78);
            rgb = base * band * ao_band + vec3<f32>(step(0.55, pow(max(dot(n, h), 0.0), 48.0)) * 0.2);
        }
        case 4u: {
            // Blueprint: luminous tint ramp with Fresnel rim, optionally tinted per part.
            let glow = clamp(0.12 + 0.55 * diffuse + 0.65 * fresnel, 0.0, 1.0);
            var tint = mix(style.cool.rgb, style.warm.rgb, glow);
            if (part_colored) {
                tint = mix(tint, base * (0.55 + 0.6 * diffuse), style.extra.x);
            }
            rgb = tint * ao + style.rim.rgb * fresnel * style.extra.y + vec3<f32>(spec);
            alpha = mix(style.warm.w, 1.0, fresnel);
        }
        default: {
            // Smooth technical shading: hemispheric ambient + key/fill diffuse + soft specular.
            let hemi = 0.5 + 0.5 * n.y;
            let light = style.key.w * (0.7 + 0.3 * hemi) + 0.72 * diffuse + fill;
            rgb = base * light * ao + vec3<f32>(spec);
        }
    }
    return vec4<f32>(clamp(rgb, vec3<f32>(0.0), vec3<f32>(1.0)), clamp(alpha, 0.0, 1.0));
}
"""
)

_XRAY_COMPOSITE_WGSL = (
    _FULLSCREEN_WGSL
    + _STYLE_STRUCT_WGSL
    + """
@group(0) @binding(1) var accum_tex: texture_2d<f32>;

@fragment
fn fs_xray_composite(@builtin(position) frag: vec4<f32>) -> @location(0) vec4<f32> {
    let acc = textureLoad(accum_tex, vec2<i32>(frag.xy), 0);
    if (acc.a <= 1e-5) {
        return vec4<f32>(0.0);
    }
    let tint = acc.rgb / acc.a;
    let density = 1.0 - exp(-acc.a * style.extra.w);
    let rgb = mix(tint, style.rim.rgb, 0.6 * density * density);
    return vec4<f32>(clamp(rgb, vec3<f32>(0.0), vec3<f32>(1.0)), clamp(density * style.warm.w, 0.0, 1.0));
}
"""
)


@dataclass(frozen=True)
class ShadedRender:
    """Result of :func:`render_shaded_view`: one transparent raster per requested theme.

    ``rect`` is ``(u_min, v_min, u_max, v_max)`` in model millimetres on the
    ``build123d.Drawing`` projection plane (``u`` along the view's right axis, ``v`` along its
    up axis, both relative to the projection origin), i.e. the exact frame the vector edges of
    the matching ``SheetView`` are expressed in.
    """

    style: ViewStyle
    images: Mapping[Theme, Image.Image]
    rect: tuple[float, float, float, float]
    adapter_name: str

    def to_svg(self) -> str:
        """Return a ``<g class="shaded-view">`` of ``<image>`` layers in Drawing coordinates.

        With more than one theme each image carries ``shaded-layer-dark`` /
        ``shaded-layer-white`` so the sheet CSS shows only the layer matching the active
        theme; a single-theme render is always visible.
        """
        from build123d_techdraw.renderers.theme import SHADED_LAYER_CLASSES

        u0, v0, u1, v1 = self.rect
        dual = len(self.images) > 1
        layers = []
        for theme, image in self.images.items():
            css = f"shaded-layer {SHADED_LAYER_CLASSES[theme]}" if dual else "shaded-layer"
            layers.append(
                f'<image class="{css}" x="{u0:.4f}" y="{-v1:.4f}" width="{u1 - u0:.4f}" '
                f'height="{v1 - v0:.4f}" preserveAspectRatio="none" '
                f'href="data:image/png;base64,{_png_base64(image)}"/>'
            )
        return f'<g class="shaded-view shaded-{self.style.value}">{"".join(layers)}</g>'


def _png_base64(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _coerce_theme(theme: Theme | str) -> Theme:
    from build123d_techdraw.renderers.theme import Theme

    if isinstance(theme, Theme):
        return theme
    key = theme.strip().lower()
    if key in ("print", "white", "iso_white"):
        return Theme.ISO_WHITE
    if key in ("dark", "blueprint", "dark_blueprint"):
        return Theme.DARK_BLUEPRINT
    raise ValueError(f"Unknown theme {theme!r}; expected 'dark_blueprint' or 'iso_white'.")


def _parse_color(spec: ColorSpec) -> Vec3:
    """Parse ``'#RRGGBB'`` / CSS colour names or ``(r, g, b)`` floats in ``[0, 1]``."""
    if isinstance(spec, str):
        r, g, b = ImageColor.getrgb(spec)[:3]
        return (r / 255.0, g / 255.0, b / 255.0)
    if len(spec) != 3 or not all(0.0 <= float(c) <= 1.0 for c in spec):
        raise ValueError(f"RGB colour tuples must hold three floats in [0, 1], got {spec!r}.")
    return (float(spec[0]), float(spec[1]), float(spec[2]))


def resolve_part_colors(
    part_names: Sequence[str],
    *,
    color_by_part: bool = False,
    part_colors: PartColors | None = None,
) -> NDArray[np.float32]:
    """Return ``(P, 4)`` RGBA per part; alpha ``1`` marks an explicit colour, ``0.5`` the theme default.

    ``part_colors`` may be a sequence (cycled by part index) or a mapping of part label to colour.
    Parts without an explicit colour use :data:`ENGINEERING_BOM_PALETTE` when ``color_by_part`` is
    set, otherwise the style's theme colour.
    """
    out = np.zeros((len(part_names), 4), dtype=np.float32)
    out[:, 3] = 0.5
    for idx, name in enumerate(part_names):
        spec: ColorSpec | None = None
        if isinstance(part_colors, Mapping):
            spec = part_colors.get(name)
        elif part_colors:
            spec = part_colors[idx % len(part_colors)]
        if spec is None and color_by_part:
            spec = ENGINEERING_BOM_PALETTE[idx % len(ENGINEERING_BOM_PALETTE)]
        if spec is not None:
            out[idx, :3] = _parse_color(spec)
            out[idx, 3] = 1.0
    return out


def _ssao_kernel() -> NDArray[np.float32]:
    """Deterministic golden-spiral hemisphere kernel, denser near the shaded point."""
    i = np.arange(_SSAO_SAMPLES, dtype=np.float64)
    z = 0.15 + 0.85 * (1.0 - (i + 0.5) / _SSAO_SAMPLES)
    r = np.sqrt(1.0 - z * z)
    phi = i * math.pi * (3.0 - math.sqrt(5.0))
    length = 0.15 + 0.85 * ((i + 1.0) / _SSAO_SAMPLES) ** 2
    kernel = np.zeros((_SSAO_SAMPLES, 4), dtype=np.float32)
    kernel[:, 0] = r * np.cos(phi) * length
    kernel[:, 1] = r * np.sin(phi) * length
    kernel[:, 2] = z * length
    return kernel


def _downsample(rgba: NDArray[np.uint8], factor: int) -> NDArray[np.uint8]:
    """Box-filter an RGBA frame by ``factor`` with premultiplied alpha (no dark fringes)."""
    if factor == 1:
        return rgba
    h, w = rgba.shape[0] // factor, rgba.shape[1] // factor
    f = rgba.astype(np.float32) / 255.0
    alpha = f[..., 3:4]
    premul = np.concatenate([f[..., :3] * alpha, alpha], axis=-1)
    blocks = premul.reshape(h, factor, w, factor, 4).mean(axis=(1, 3))
    a = blocks[..., 3:4]
    rgb = np.where(a > 1e-6, blocks[..., :3] / np.maximum(a, 1e-6), 0.0)
    out = np.concatenate([rgb, a], axis=-1)
    return np.clip(np.rint(out * 255.0), 0, 255).astype(np.uint8)


def _require_gpu(style: ViewStyle, gpu: bool | None) -> None:
    """Raise an actionable error when a shaded ``style`` cannot use the GPU."""
    hint = (
        f"ViewStyle.{style.name} is rendered with wgpu shaders; use style=ViewStyle.WIREFRAME "
        "for a pure vector drawing"
    )
    if gpu is False:
        raise RuntimeError(f"{hint}, or pass gpu=None/True instead of gpu=False.")
    if gpu is None and is_gpu_enabled(None):
        return
    # Raises ImportError (missing 'gpu' extra) or RuntimeError (no adapter) with install hints.
    is_gpu_enabled(True)
    if gpu is None:
        raise RuntimeError(f"{hint}, or unset BUILD123D_TECHDRAW_GPU=0 to allow GPU rendering.")


class _Pipelines:
    """Compiled render pipelines for one ``wgpu`` device."""

    def __init__(self, device: Any, wgpu: Any) -> None:
        mesh = device.create_shader_module(code=_MESH_WGSL)
        vertex = {
            "module": mesh,
            "entry_point": "vs_mesh",
            "buffers": [
                {
                    "array_stride": 40,
                    "step_mode": wgpu.VertexStepMode.vertex,
                    "attributes": [
                        {"format": wgpu.VertexFormat.float32x3, "offset": 0, "shader_location": 0},
                        {"format": wgpu.VertexFormat.float32x3, "offset": 12, "shader_location": 1},
                        {"format": wgpu.VertexFormat.float32x4, "offset": 24, "shader_location": 2},
                    ],
                }
            ],
        }
        primitive = {
            "topology": wgpu.PrimitiveTopology.triangle_list,
            "front_face": wgpu.FrontFace.ccw,
            "cull_mode": wgpu.CullMode.none,
        }
        self.gbuffer = device.create_render_pipeline(
            layout="auto",
            vertex=vertex,
            primitive=primitive,
            depth_stencil={
                "format": wgpu.TextureFormat.depth32float,
                "depth_write_enabled": True,
                "depth_compare": wgpu.CompareFunction.less,
            },
            fragment={
                "module": mesh,
                "entry_point": "fs_gbuffer",
                "targets": [
                    {"format": wgpu.TextureFormat.rgba8unorm},
                    {"format": wgpu.TextureFormat.rgba16float},
                ],
            },
        )
        additive = {
            "src_factor": wgpu.BlendFactor.one,
            "dst_factor": wgpu.BlendFactor.one,
            "operation": wgpu.BlendOperation.add,
        }
        self.xray = device.create_render_pipeline(
            layout="auto",
            vertex=vertex,
            primitive=primitive,
            fragment={
                "module": mesh,
                "entry_point": "fs_xray",
                "targets": [
                    {
                        "format": wgpu.TextureFormat.rgba16float,
                        "blend": {"color": additive, "alpha": additive},
                    }
                ],
            },
        )
        self.ssao = self._screen_pipeline(device, wgpu, _SSAO_WGSL, "fs_ssao", "r8unorm")
        self.composite = self._screen_pipeline(
            device, wgpu, _COMPOSITE_WGSL, "fs_composite", "rgba8unorm"
        )
        self.xray_composite = self._screen_pipeline(
            device, wgpu, _XRAY_COMPOSITE_WGSL, "fs_xray_composite", "rgba8unorm"
        )

    @staticmethod
    def _screen_pipeline(device: Any, wgpu: Any, code: str, entry: str, target: str) -> Any:
        module = device.create_shader_module(code=code)
        return device.create_render_pipeline(
            layout="auto",
            vertex={"module": module, "entry_point": "vs_fullscreen", "buffers": []},
            primitive={"topology": wgpu.PrimitiveTopology.triangle_list},
            fragment={"module": module, "entry_point": entry, "targets": [{"format": target}]},
        )


_PIPELINE_CACHE: list[tuple[Any, _Pipelines]] = []


def _pipelines_for(device: Any, wgpu: Any) -> _Pipelines:
    for cached_device, pipelines in _PIPELINE_CACHE:
        if cached_device is device:
            return pipelines
    pipelines = _Pipelines(device, wgpu)
    _PIPELINE_CACHE.append((device, pipelines))
    return pipelines


class _Frame:
    """Offscreen render targets and command helpers for one shaded frame."""

    def __init__(self, device: Any, wgpu: Any, width: int, height: int) -> None:
        self.device = device
        self.wgpu = wgpu
        self.size = (width, height, 1)
        self.width = width
        self.height = height

    def texture(self, fmt: str, *, sampled: bool = True, readback: bool = False) -> Any:
        usage = self.wgpu.TextureUsage.RENDER_ATTACHMENT
        if sampled:
            usage |= self.wgpu.TextureUsage.TEXTURE_BINDING
        if readback:
            usage |= self.wgpu.TextureUsage.COPY_SRC
        return self.device.create_texture(size=self.size, format=fmt, usage=usage)

    def uniform(self, data: NDArray[np.float32]) -> Any:
        return self.device.create_buffer_with_data(
            data=np.ascontiguousarray(data, dtype=np.float32), usage=self.wgpu.BufferUsage.UNIFORM
        )

    def bind(self, pipeline: Any, buffer: Any, textures: Sequence[Any]) -> Any:
        entries: list[dict[str, Any]] = [{"binding": 0, "resource": {"buffer": buffer}}]
        entries.extend(
            {"binding": i + 1, "resource": tex.create_view()} for i, tex in enumerate(textures)
        )
        return self.device.create_bind_group(
            layout=pipeline.get_bind_group_layout(0), entries=entries
        )

    def attachment(self, texture: Any) -> dict[str, Any]:
        return {
            "view": texture.create_view(),
            "load_op": self.wgpu.LoadOp.clear,
            "store_op": self.wgpu.StoreOp.store,
            "clear_value": (0.0, 0.0, 0.0, 0.0),
        }

    def fullscreen(self, encoder: Any, pipeline: Any, bind_group: Any, target: Any) -> None:
        rpass = encoder.begin_render_pass(color_attachments=[self.attachment(target)])
        rpass.set_pipeline(pipeline)
        rpass.set_bind_group(0, bind_group)
        rpass.draw(3)
        rpass.end()

    def read_rgba(self, texture: Any) -> NDArray[np.uint8]:
        raw = self.device.queue.read_texture(
            {"texture": texture}, {"bytes_per_row": self.width * 4}, self.size
        )
        return np.frombuffer(raw, dtype=np.uint8).reshape(self.height, self.width, 4).copy()


def _render_frames(
    mesh: TessellatedMesh,
    camera: OrthoCamera,
    part_rgba: NDArray[np.float32],
    style: ViewStyle,
    themes: Sequence[Theme],
    *,
    ssao: bool,
) -> tuple[dict[Theme, NDArray[np.uint8]], str]:
    """Run the G-buffer / SSAO / composite passes; return raw RGBA frames per theme."""
    import wgpu

    adapter, device = get_gpu_device()
    pipelines = _pipelines_for(device, wgpu)
    frame = _Frame(device, wgpu, camera.width_px, camera.height_px)

    vert_part = np.zeros(mesh.num_vertices, dtype=np.uint32)
    vert_part[mesh.indices.reshape(-1)] = np.repeat(mesh.part_ids, 3)
    vertices = np.hstack([mesh.positions, mesh.normals, part_rgba[vert_part]]).astype(np.float32)
    vbuf = device.create_buffer_with_data(
        data=np.ascontiguousarray(vertices), usage=wgpu.BufferUsage.VERTEX
    )
    ibuf = device.create_buffer_with_data(
        data=np.ascontiguousarray(mesh.indices.reshape(-1), dtype=np.uint32),
        usage=wgpu.BufferUsage.INDEX,
    )

    def scene_uniform(theme: Theme) -> Any:
        tint = (*_palette(style, theme).base, 1.0)
        return frame.uniform(
            np.concatenate(
                [
                    camera.uniform_view_proj().reshape(-1),
                    camera.uniform_view_matrix().reshape(-1),
                    np.asarray(tint, dtype=np.float32),
                ]
            )
        )

    def draw_mesh(
        encoder: Any, pipeline: Any, theme: Theme, targets: list[Any], depth: Any
    ) -> None:
        rpass = encoder.begin_render_pass(
            color_attachments=[frame.attachment(t) for t in targets],
            depth_stencil_attachment=None
            if depth is None
            else {
                "view": depth.create_view(),
                "depth_load_op": wgpu.LoadOp.clear,
                "depth_store_op": wgpu.StoreOp.store,
                "depth_clear_value": 1.0,
            },
        )
        rpass.set_pipeline(pipeline)
        rpass.set_bind_group(0, frame.bind(pipeline, scene_uniform(theme), []))
        rpass.set_vertex_buffer(0, vbuf)
        rpass.set_index_buffer(ibuf, wgpu.IndexFormat.uint32)
        rpass.draw_indexed(int(mesh.indices.size))
        rpass.end()

    output = frame.texture("rgba8unorm", sampled=False, readback=True)
    frames: dict[Theme, NDArray[np.uint8]] = {}

    if style is ViewStyle.XRAY:
        accum = frame.texture("rgba16float")
        for theme in themes:
            encoder = device.create_command_encoder()
            draw_mesh(encoder, pipelines.xray, theme, [accum], None)
            style_buf = frame.uniform(_style_uniform(style, theme, ssao=False))
            bind = frame.bind(pipelines.xray_composite, style_buf, [accum])
            frame.fullscreen(encoder, pipelines.xray_composite, bind, output)
            device.queue.submit([encoder.finish()])
            frames[theme] = frame.read_rgba(output)
    else:
        albedo = frame.texture("rgba8unorm")
        normal = frame.texture("rgba16float")
        depth = frame.texture("depth32float", sampled=False)
        ao = frame.texture("r8unorm")
        encoder = device.create_command_encoder()
        draw_mesh(encoder, pipelines.gbuffer, themes[0], [albedo, normal], depth)
        if ssao:
            mm_per_px = 1.0 / camera.pixels_per_mm
            radius = float(np.clip(0.035 * max(camera.model_width, camera.model_height), 0.4, 25.0))
            ssao_data = np.zeros((2 + _SSAO_SAMPLES, 4), dtype=np.float32)
            ssao_data[0] = (
                0.5 * camera.view_width_mm,
                0.5 * camera.view_height_mm,
                mm_per_px,
                radius,
            )
            ssao_data[1] = (camera.z_near, camera.z_far, max(0.02 * radius, mm_per_px), 1.25)
            ssao_data[2:] = _ssao_kernel()
            ssao_bind = frame.bind(pipelines.ssao, frame.uniform(ssao_data), [normal])
            frame.fullscreen(encoder, pipelines.ssao, ssao_bind, ao)
        device.queue.submit([encoder.finish()])
        for theme in themes:
            encoder = device.create_command_encoder()
            style_buf = frame.uniform(_style_uniform(style, theme, ssao=ssao))
            bind = frame.bind(pipelines.composite, style_buf, [albedo, normal, ao])
            frame.fullscreen(encoder, pipelines.composite, bind, output)
            device.queue.submit([encoder.finish()])
            frames[theme] = frame.read_rgba(output)

    info = adapter.info
    name = str(info.get("device", "")) if isinstance(info, Mapping) else ""
    return frames, name


def render_shaded_view(
    shape: Shape[Any] | TessellatedMesh,
    look_from: Vec3,
    look_up: Vec3,
    *,
    style: ViewStyle | str = ViewStyle.SHADED_WITH_EDGES,
    shading_dpi: int = 300,
    color_by_part: bool = False,
    part_colors: PartColors | None = None,
    ssao: bool = True,
    gpu: bool | None = None,
    shading_theme: Theme | str | None = None,
    scale: float = 1.0,
    origin: Vec3 | None = None,
    max_size_px: int = 2048,
) -> ShadedRender:
    """Render a transparent technical-illustration raster of ``shape`` with ``wgpu``.

    Args:
        shape: build123d shape (``Part``, ``Solid``, multi-part ``Compound``) or a pre-built
            :class:`~build123d_techdraw.gpu.mesh.TessellatedMesh`.
        look_from, look_up: Camera vectors, as in ``build123d.Drawing`` / ``VIEW_VECTORS``.
        style: Any shaded :class:`ViewStyle` (``WIREFRAME`` has nothing to render).
        shading_dpi: Raster resolution in pixels per inch of *paper*; the longest side is
            capped at ``max_size_px`` so renders stay fast and memory-light.
        color_by_part: Colour each solid of an assembly from :data:`ENGINEERING_BOM_PALETTE`.
        part_colors: Explicit colours per part, by index (sequence) or part label (mapping).
        ssao: Enable screen-space ambient occlusion (ignored by ``XRAY``).
        gpu: ``None`` uses the GPU unless ``BUILD123D_TECHDRAW_GPU=0``; ``False`` is an error.
        shading_theme: Render one theme only; ``None`` renders both ``DARK_BLUEPRINT`` and
            ``ISO_WHITE`` so a dual-theme SVG can switch between them.
        scale: View scale (paper mm per model mm), used to convert ``shading_dpi``.
        origin: Projection origin; defaults to ``shape.center()`` like ``build123d.Drawing``
            (or the world origin for a mesh).
        max_size_px: Maximum output size in pixels along either axis.

    Raises:
        ImportError: If ``wgpu`` is not installed (``pip install build123d-techdraw[gpu]``).
        RuntimeError: If GPU rendering is disabled (``gpu=False`` / environment) or no
            adapter is available.
        ValueError: For ``ViewStyle.WIREFRAME``, an empty shape or invalid arguments.
    """
    view_style = ViewStyle.coerce(style)
    if view_style is ViewStyle.WIREFRAME:
        raise ValueError("ViewStyle.WIREFRAME is drawn with vectors only; choose a shaded style.")
    if shading_dpi <= 0 or scale <= 0.0 or max_size_px <= 0:
        raise ValueError("shading_dpi, scale and max_size_px must be positive.")
    _require_gpu(view_style, gpu)
    from build123d_techdraw.renderers.theme import Theme

    themes = (
        (Theme.DARK_BLUEPRINT, Theme.ISO_WHITE)
        if shading_theme is None
        else (_coerce_theme(shading_theme),)
    )

    px_per_model_mm = scale * shading_dpi / _MM_PER_INCH
    if isinstance(shape, TessellatedMesh):
        mesh = shape
        center: Vec3 = origin if origin is not None else (0.0, 0.0, 0.0)
    else:
        bbox = shape.bounding_box()
        deflection = max(0.5 / px_per_model_mm, float(bbox.diagonal) * 1e-4, 1e-3)
        mesh = tessellate_shape(
            shape, linear_deflection=min(deflection, float(bbox.diagonal) * 0.01 + 1e-3)
        )
        c = shape.center() if isinstance(shape, _HasCenter) else bbox.center()
        center = origin if origin is not None else (float(c.X), float(c.Y), float(c.Z))
    if mesh.num_triangles == 0:
        raise ValueError("Cannot shade a shape without faces.")

    # Size the raster from the unpadded projected extent, then frame with a few pixels of margin.
    probe = compute_ortho_camera_for_points(mesh.positions, look_from, look_up, 64, 64)
    longest = max(probe.model_width, probe.model_height) * px_per_model_mm
    if longest + 2.0 * _PADDING_PX > max_size_px:
        px_per_model_mm *= (max_size_px - 2.0 * _PADDING_PX) / longest
    pad_mm = _PADDING_PX / px_per_model_mm
    width = max(1, math.ceil((probe.model_width + 2.0 * pad_mm) * px_per_model_mm))
    height = max(1, math.ceil((probe.model_height + 2.0 * pad_mm) * px_per_model_mm))
    factor = _SUPERSAMPLE if width * height <= _SUPERSAMPLE_MAX_PIXELS else 1
    camera = compute_ortho_camera_for_points(
        mesh.positions, look_from, look_up, width * factor, height * factor, padding_mm=pad_mm
    )

    part_rgba = resolve_part_colors(
        mesh.part_names, color_by_part=color_by_part, part_colors=part_colors
    )
    frames, adapter_name = _render_frames(mesh, camera, part_rgba, view_style, themes, ssao=ssao)
    images = {
        theme: Image.fromarray(_downsample(rgba, factor), mode="RGBA")
        for theme, rgba in frames.items()
    }

    o = np.asarray(center, dtype=np.float64)
    cu = camera.model_center_2d[0] - float(o @ np.asarray(camera.right))
    cv = camera.model_center_2d[1] - float(o @ np.asarray(camera.up))
    hw, hh = 0.5 * camera.view_width_mm, 0.5 * camera.view_height_mm
    return ShadedRender(
        style=view_style,
        images=images,
        rect=(cu - hw, cv - hh, cu + hw, cv + hh),
        adapter_name=adapter_name,
    )


def shade_view_svg(
    inner_svg: str,
    shape: Shape[Any],
    look_from: Vec3,
    look_up: Vec3,
    *,
    scale: float,
    **shading: Unpack[ShadingKwargs],
) -> tuple[str, ViewStyle | None]:
    """Prepend a shaded raster layer to a view's projected SVG.

    Returns ``(svg, style)``; ``style`` is ``None`` (and ``inner_svg`` unchanged) for
    ``ViewStyle.WIREFRAME``. ``ViewStyle.SHADED`` drops the vector edges, every other style
    keeps them on top of the raster.
    """
    style = ViewStyle.coerce(shading.get("style", ViewStyle.WIREFRAME))
    if style is ViewStyle.WIREFRAME:
        return inner_svg, None
    shading["style"] = style
    layer = render_shaded_view(shape, look_from, look_up, scale=scale, **shading).to_svg()
    return (layer if style is ViewStyle.SHADED else layer + inner_svg), style
