import 'package:flutter/material.dart';

ThemeData odessyTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final scheme = ColorScheme.fromSeed(
      seedColor: const Color(0xff9bb9a7),
      brightness: brightness,
      surface: dark ? const Color(0xff17191b) : const Color(0xfffafaf8));
  final base = ThemeData(brightness: brightness).textTheme;
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    brightness: brightness,
    scaffoldBackgroundColor: scheme.surface,
    fontFamily: 'OdessySans',
    // Every style carries the bundled family; an undefined style would fall back
    // to a font this application does not ship.
    textTheme: base
        .copyWith(
            bodyMedium: base.bodyMedium?.copyWith(fontSize: 14, height: 1.5),
            bodySmall: base.bodySmall?.copyWith(fontSize: 12, height: 1.45),
            titleLarge: base.titleLarge?.copyWith(
                fontSize: 25, fontWeight: FontWeight.w600, letterSpacing: -.6),
            titleMedium: base.titleMedium?.copyWith(
                fontSize: 15, fontWeight: FontWeight.w600),
            labelLarge: base.labelLarge?.copyWith(fontSize: 13),
            labelMedium: base.labelMedium?.copyWith(fontSize: 12))
        .apply(
            fontFamily: 'OdessySans',
            fontFamilyFallback: const ['OdessySans']),
    dividerColor: dark ? const Color(0xff303335) : const Color(0xffe0e2dc),
    inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: dark ? const Color(0xff202326) : Colors.white,
        border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide: BorderSide.none),
        contentPadding: const EdgeInsets.all(16)),
    filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 18),
            shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(10)))),
    cardTheme: CardThemeData(
        elevation: 0,
        margin: EdgeInsets.zero,
        color: dark ? const Color(0xff202326) : Colors.white,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(14),
            side: BorderSide(
                color:
                    dark ? const Color(0xff34383a) : const Color(0xffe0e2dc)))),
  );
}

/// Shared desktop geometry; nested radii subtract their inset.
abstract final class OdessyTokens {
  static const spaceSmall = 4.0;
  static const spaceCompact = 8.0;
  static const spaceMedium = 12.0;
  static const spaceRegular = 16.0;
  static const spaceLarge = 24.0;
  static const radiusSmall = 10.0;
  static const radiusMedium = 14.0;
  static const radiusLarge = 20.0;
  static const accent = Color(0xff537d67);
}
