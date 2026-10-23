import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:odessy_desktop/app/theme.dart';
import 'package:odessy_desktop/widgets/glass_surface.dart';

void main() {
  for (final brightness in Brightness.values) {
    testWidgets('glass clips its filter in $brightness', (tester) async {
      await tester.pumpWidget(MaterialApp(
        theme: odessyTheme(brightness),
        home: const Scaffold(body: GlassSurface(child: Text('Navigation'))),
      ));
      expect(find.byType(BackdropFilter), findsOneWidget);
      expect(
          find.ancestor(
              of: find.byType(BackdropFilter),
              matching: find.byType(ClipRRect)),
          findsOneWidget);
      expect(find.text('Navigation'), findsOneWidget);
    });
  }
  for (final mode in ['preference', 'contrast', 'motion']) {
    testWidgets('$mode uses opaque glass without filtering', (tester) async {
      await tester.pumpWidget(MaterialApp(
          home: MediaQuery(
        data: MediaQueryData(
            highContrast: mode == 'contrast',
            disableAnimations: mode == 'motion'),
        child: GlassSurface(
            reduceTransparency: mode == 'preference',
            child: const Text('Readable')),
      )));
      expect(find.byType(BackdropFilter), findsNothing);
      final decoration = tester
          .widget<DecoratedBox>(find
              .descendant(
                  of: find.byType(GlassSurface),
                  matching: find.byType(DecoratedBox))
              .first)
          .decoration as BoxDecoration;
      expect(decoration.color!.a, 1);
      expect(find.text('Readable'), findsOneWidget);
    });
  }
}
