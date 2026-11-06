# Core API

::: build123d_techdraw.core.sheet
    options:
      members:
        - DrawingSheet
        - SheetSize

::: build123d_techdraw.core.view
    options:
      members:
        - SheetView
        - ViewType

::: build123d_techdraw.core.title_block
    options:
      members:
        - TitleBlockInfo
        - BOMItem
        - RevisionEntry

::: build123d_techdraw.core.booklet
    options:
      members:
        - DrawingBooklet

::: build123d_techdraw.core.scale
    options:
      members:
        - format_iso_scale
        - find_closest_iso_scale
        - calculate_auto_scale
