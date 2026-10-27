import 'package:flutter/material.dart';
import '../app/theme.dart';

/// Shared top-aligned page geometry; never owns a controller or native operation.
class DesktopPageScaffold extends StatelessWidget {
  const DesktopPageScaffold(
      {super.key,
      required this.title,
      required this.child,
      this.description,
      this.actions = const [],
      this.maxWidth = 1120});
  final String title;
  final String? description;
  final Widget child;
  final List<Widget> actions;
  final double maxWidth;
  @override
  Widget build(BuildContext context) => SingleChildScrollView(
      padding: const EdgeInsets.all(OdessyTokens.spaceLarge),
      child: Align(
          alignment: Alignment.topLeft,
          child: ConstrainedBox(
              constraints: BoxConstraints(maxWidth: maxWidth),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text(title, style: Theme.of(context).textTheme.titleLarge),
                    if (description != null) ...[
                      const SizedBox(height: 8),
                      Text(description!,
                          style: Theme.of(context).textTheme.bodySmall)
                    ],
                    if (actions.isNotEmpty) ...[
                      const SizedBox(height: 16),
                      Wrap(spacing: 12, runSpacing: 8, children: actions)
                    ],
                    const SizedBox(height: 24),
                    child,
                  ]))));
}
