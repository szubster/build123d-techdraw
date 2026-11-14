# Rule: Prefer Ecosystem & Lightweight Libraries

Before implementing custom geometry, projection, tessellation, parsing, or file-export logic—or adding heavy general-purpose dependencies:

1. **Check the `build123d` ecosystem first:** Inspect `build123d` and its existing transitive dependencies (`ocpsvg`, `ezdxf`, `svgpathtools`, `OCP`, `numpy`, `scipy`) for native support before writing custom wrappers, temp-file round-trips, or manual tessellation.
2. **Prefer lightweight, purpose-built libraries:** Do not pull in heavy multi-purpose frameworks (e.g., `matplotlib`) for a single narrow task (e.g., vector PDF generation) when a lightweight, focused library exists.
3. **Avoid fragile post-processing:** Prefer structured/native library APIs (e.g., `ocpsvg` / `ezdxf.path` / native PDF outlines) over regex XML mangling, temp-file I/O, or monkey-patching private internals.
