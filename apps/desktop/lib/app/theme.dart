import 'package:flutter/material.dart';

ThemeData odessyTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final scheme = ColorScheme.fromSeed(
      seedColor: const Color(0xff9bb9a7),
      brightness: brightness,
      surface: dark ? const Color(0xff17191b) : const Color(0xfffafaf8));
  return ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    brightness: brightness,
    scaffoldBackgroundColor: scheme.surface,
    fontFamily: 'OdessySans',
    textTheme: const TextTheme(
        bodyMedium: TextStyle(fontSize: 14, height: 1.5),
        titleLarge: TextStyle(
            fontSize: 25, fontWeight: FontWeight.w600, letterSpacing: -.6)),
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
