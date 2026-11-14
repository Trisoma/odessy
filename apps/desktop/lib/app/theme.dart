import 'package:flutter/material.dart';

ThemeData odessyTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final scheme = ColorScheme.fromSeed(
          seedColor: const Color(0xff9ce4c3), brightness: brightness)
      .copyWith(
    primary: dark ? const Color(0xff9ce4c3) : const Color(0xff23684d),
    onPrimary: dark ? const Color(0xff111d25) : Colors.white,
    tertiary: dark ? const Color(0xffffd18a) : const Color(0xff785018),
    tertiaryContainer: dark ? const Color(0xff544322) : const Color(0xffffe5b5),
    secondary: dark ? const Color(0xff9bcff5) : const Color(0xff245e87),
    surface: dark ? const Color(0xff111d25) : const Color(0xfff5f8f9),
    onSurface: dark ? const Color(0xffedf5f4) : const Color(0xff172b35),
    onSurfaceVariant: dark ? const Color(0xffb0c5cc) : const Color(0xff48616c),
    surfaceContainerLow:
        dark ? const Color(0xff15252e) : const Color(0xffedf3f5),
    surfaceContainer: dark ? const Color(0xff1c303a) : Colors.white,
    surfaceContainerHighest:
        dark ? const Color(0xff243e49) : const Color(0xffdce9ee),
    secondaryContainer:
        dark ? const Color(0xff243e49) : const Color(0xffdce9ee),
    onSecondaryContainer:
        dark ? const Color(0xffedf5f4) : const Color(0xff172b35),
    outlineVariant: dark ? const Color(0xff36515e) : const Color(0xffc2d3da),
  );
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
                fontSize: 28, fontWeight: FontWeight.w600, letterSpacing: -.6),
            titleMedium: base.titleMedium
                ?.copyWith(fontSize: 15, fontWeight: FontWeight.w700),
            labelLarge: base.labelLarge?.copyWith(fontSize: 13),
            labelMedium: base.labelMedium?.copyWith(fontSize: 12))
        .apply(
            fontFamily: 'OdessySans', fontFamilyFallback: const ['OdessySans']),
    dividerColor: dark ? const Color(0xff36515e) : const Color(0xffc2d3da),
    inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: dark ? const Color(0xff1c303a) : Colors.white,
        border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(12),
            borderSide: BorderSide.none),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 14, vertical: 14)),
    filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
            padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 12),
            shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(10)))),
    textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
            foregroundColor: scheme.onSurfaceVariant,
            shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(7)))),
    iconButtonTheme: IconButtonThemeData(
        style: IconButton.styleFrom(
            foregroundColor: scheme.onSurfaceVariant,
            minimumSize: const Size(40, 40))),
    listTileTheme: ListTileThemeData(
        iconColor: scheme.onSurfaceVariant,
        selectedColor: scheme.primary,
        selectedTileColor: scheme.secondaryContainer,
        titleTextStyle: TextStyle(
            fontFamily: 'OdessySans', fontSize: 13, color: scheme.onSurface)),
    cardTheme: CardThemeData(
        elevation: 0,
        margin: EdgeInsets.zero,
        color: dark ? const Color(0xff1c303a) : Colors.white,
        shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
            side: BorderSide(
                color:
                    dark ? const Color(0xff36515e) : const Color(0xffc2d3da)))),
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
