import 'dart:ui';

import 'package:flutter/material.dart';

import '../app/theme.dart';

/// Restrained navigation chrome; never use behind long-form content.
/// This is a Flutter frosted material, not native Apple Liquid Glass.
class GlassSurface extends StatelessWidget {
  const GlassSurface({
    super.key,
    required this.child,
    this.padding = EdgeInsets.zero,
    this.radius = OdessyTokens.radiusLarge,
    this.reduceTransparency = false,
  });

  final Widget child;
  final EdgeInsetsGeometry padding;
  final double radius;
  final bool reduceTransparency;

  @override
  Widget build(BuildContext context) {
    final media = MediaQuery.maybeOf(context);
    final opaque = reduceTransparency ||
        (media?.highContrast ?? false) ||
        (media?.disableAnimations ?? false);
    final dark = Theme.of(context).brightness == Brightness.dark;
    final base = dark ? const Color(0xff24282b) : const Color(0xfff8faf9);
    final panel = DecoratedBox(
      decoration: BoxDecoration(
        color: base.withValues(alpha: opaque ? 1 : .86),
        borderRadius: BorderRadius.circular(radius),
        border: Border.all(
          color: dark ? const Color(0xff454b4e) : const Color(0xffd6ded9),
        ),
      ),
      child: Material(
          type: MaterialType.transparency,
          child: Padding(padding: padding, child: child)),
    );
    return ClipRRect(
      borderRadius: BorderRadius.circular(radius),
      child: opaque
          ? panel
          : BackdropFilter(
              filter: ImageFilter.blur(sigmaX: 10, sigmaY: 10),
              child: panel,
            ),
    );
  }
}
