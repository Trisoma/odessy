# Desktop visual foundation

Odessy uses restrained Flutter frosted material inspired by Apple's material hierarchy, not native Liquid Glass. Glass belongs to navigation and control chrome: sidebar, header and composer frame. Transcripts, approval details and settings fields retain readable opaque surfaces. Avoid stacked filters and per-card blur. GlassSurface clips its modest 10px filter to its own rounded bounds.

OdessyTokens provides spacing 4/8/12/16/24 and radii 10/14/20. The palette remains neutral with a muted green accent. For concentric nested corners, subtract the inset from the outer radius. Preserve visible focus, selectable text and text scaling; status must also have text or icons.

GlassSurface accepts an explicit reduceTransparency preference and automatically becomes opaque for high contrast or disabled animations. These signals do not detect every OS transparency preference; callers supply that preference where available. The opaque mode removes the filter entirely. Inspect both themes, large text, keyboard traversal and fallback modes in composed screens.

The device sidebar and single main pane follow the desktop plan. OpenClaw and deepseek-harness provide structural reference points for separating presentation from orchestration and execution, not runtime dependencies or copied code. Flutter widgets consume typed client/controller state; Python, Rust and TypeScript retain authority. Shared widgets live in lib/widgets and tokens in lib/app/theme.dart.

References: [Apple materials](https://developer.apple.com/design/human-interface-guidelines/materials), [Adopting Liquid Glass](https://developer.apple.com/documentation/technologyoverviews/adopting-liquid-glass). These are portable visual interpretations, not native API equivalence claims.

Tests in design_glass_test.dart cover light/dark clipping and opaque fallbacks for preference, high contrast and disabled animations.
