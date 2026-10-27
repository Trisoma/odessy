import 'package:flutter/material.dart';
import '../app/theme.dart';

/// The common settings shell: top-aligned rail, responsive navigation and a bounded form.
class SettingsScaffold extends StatelessWidget {
  const SettingsScaffold(
      {super.key,
      required this.title,
      required this.description,
      required this.navigation,
      required this.compactNavigation,
      required this.child,
      this.breakpoint = 760});
  final String title, description;
  final Widget navigation, compactNavigation, child;
  final double breakpoint;
  @override
  Widget build(BuildContext context) => LayoutBuilder(builder: (context, box) {
        final narrow = box.maxWidth < breakpoint;
        final body = SingleChildScrollView(
            key: const Key('settings-content-scroll'),
            padding: const EdgeInsets.all(OdessyTokens.spaceLarge),
            child: Align(
                alignment: Alignment.topLeft,
                child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 720),
                    child: child)));
        return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Padding(
                  padding: const EdgeInsets.all(OdessyTokens.spaceLarge),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(title,
                            style: Theme.of(context).textTheme.titleLarge),
                        const SizedBox(height: 8),
                        Text(description,
                            style: Theme.of(context).textTheme.bodySmall)
                      ])),
              if (narrow)
                Padding(
                    padding: const EdgeInsets.symmetric(
                        horizontal: OdessyTokens.spaceLarge),
                    child: compactNavigation),
              Expanded(
                  child: narrow
                      ? body
                      : Row(
                          crossAxisAlignment: CrossAxisAlignment.stretch,
                          children: [
                              Padding(
                                  padding:
                                      const EdgeInsets.only(left: 8, top: 12),
                                  child: navigation),
                              const VerticalDivider(width: 1),
                              Expanded(child: body)
                            ])),
            ]);
      });
}
