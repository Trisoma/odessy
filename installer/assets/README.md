# Application icon

`application_icon.png` is the checked-in source for the native application icon.
`odessy-boat.ico` retains its existing filename for packaging compatibility and
contains 16, 24, 32, 48, 64, 128 and 256 pixel Windows frames.

Windows runner resource: `apps/desktop/windows/runner/resources/app_icon.ico`.
macOS assets: `apps/desktop/macos/Runner/Assets.xcassets/AppIcon.appiconset/`.
Non-square source artwork is centered on a transparent square without stretching.

Rebuild the desktop and installer payload to distribute changed icons. This does
not replace in-app Flutter branding. Linux launcher integration remains separate.
