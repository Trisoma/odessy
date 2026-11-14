import 'package:flutter/material.dart';

/// Responsive settings rail. Forms and their draft ownership remain feature-owned.
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
            padding: EdgeInsets.symmetric(
                horizontal: narrow ? 20 : 48, vertical: 32),
            child: Align(
                alignment: Alignment.topCenter,
                child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 930),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          if (narrow) ...[
                            Semantics(
                                header: true, child: const Text('Preferences')),
                            const SizedBox(height: 12),
                            compactNavigation,
                            const SizedBox(height: 24)
                          ],
                          Semantics(
                              header: true,
                              child: Text(title,
                                  style: Theme.of(context)
                                      .textTheme
                                      .headlineMedium
                                      ?.copyWith(
                                          fontWeight: FontWeight.w700,
                                          letterSpacing: -.8))),
                          const SizedBox(height: 12),
                          Text(description,
                              style: TextStyle(
                                  color: Theme.of(context)
                                      .colorScheme
                                      .onSurfaceVariant)),
                          const SizedBox(height: 28),
                          child,
                        ]))));
        if (narrow) return body;
        return Row(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Material(
              color: Theme.of(context).colorScheme.surfaceContainerLow,
              child: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 24, 12, 12),
                  child: navigation)),
          Expanded(child: body),
        ]);
      });
}
